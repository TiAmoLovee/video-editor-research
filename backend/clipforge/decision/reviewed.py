"""人工试听通过后的独立候选及规则重评分；不改写原 analysis/candidates。"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from clipforge.decision.audio import measure_audio
from clipforge.decision.candidates import content_hash
from clipforge.decision.scorers import RuleScorer
from clipforge.decision.scoring import load_config, validate_scored


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rebuild(analysis, scored, parent_id, acceptance, render, selected_video, input_clip):
    validate_scored(scored, analysis)
    if (acceptance.get('version') != 'human-audition-acceptance-v1'
            or acceptance.get('rendered_boundary_review') != 'accepted_by_user'
            or not acceptance.get('user_feedback_verbatim')
            or acceptance.get('task_id') != render.get('task_id')
            or acceptance.get('fps') != 30 or render.get('fps') != 30):
        raise ValueError('缺少有效的人工试听结论或时间基准不一致')
    first, last = acceptance['start_frame'], acceptance['end_frame_exclusive']
    if type(first) is not int or type(last) is not int or not 0 <= first < last:
        raise ValueError('人工选定帧范围无效')
    start, end, duration = first/30, last/30, (last-first)/30
    if not 15 <= duration <= 90:
        raise ValueError('修正片段必须为 15–90 秒')
    for key, expected in [('start_seconds', start), ('end_seconds_exclusive', end), ('duration_seconds', duration)]:
        value = acceptance.get(key)
        if type(value) not in (int, float) or not math.isclose(value, expected, abs_tol=1e-8, rel_tol=0):
            raise ValueError('人工记录的秒数与帧数不符')
    parent = next((c for c in scored['candidates'] if c['id'] == parent_id), None)
    if parent is None or not parent['start'] <= start < end <= parent['end']:
        raise ValueError('修正范围必须属于指定原候选')
    selected_hash = file_hash(selected_video)
    if selected_hash != acceptance.get('selected_sha256'):
        raise ValueError('实际试听文件与人工确认文件不一致')
    input_hash = file_hash(input_clip)
    if input_hash != acceptance.get('input_clip_sha256') or input_hash != render.get('source_sha256'):
        raise ValueError('试听源文件身份不一致')
    variants = [v for v in render['variants'] if v['sha256'] == selected_hash]
    if (len(variants) != 1 or variants[0]['start_frame'] != first
            or variants[0]['end_frame_exclusive'] != last
            or variants[0]['video_frames_decoded'] != last-first):
        raise ValueError('试听渲染记录与选定范围不一致')

    words, adjustments, sentences = [], [], []
    word_map = {w['id']: w for w in analysis['words']}
    for sentence in analysis['sentences']:
        if sentence['id'] not in parent['source_sentences']:
            continue
        members = []
        for wid in sentence['word_ids']:
            raw = word_map[wid]
            if raw['start'] >= end or raw['end'] <= start:
                continue
            lo, hi = max(start, raw['start']), min(end, raw['end'])
            word = {**deepcopy(raw), 'start': round(lo-start, 9), 'end': round(hi-start, 9)}
            if lo != raw['start'] or hi != raw['end']:
                adjustments.append({'word_id': wid, 'original_start': raw['start'], 'original_end': raw['end'],
                                    'used_start': lo, 'used_end': hi,
                                    'reason': 'human_accepted_render_boundary; original ASR unchanged'})
            words.append(word); members.append(word)
        if members:
            sentences.append({'id': sentence['id'], 'start': min(w['start'] for w in members),
                              'end': max(w['end'] for w in members),
                              'text': ''.join(w['text'] for w in members), 'word_ids': [w['id'] for w in members]})
    if not words:
        raise ValueError('修正范围中没有可追溯的转写词')
    text = '\n'.join(s['text'] for s in sentences)
    identity = {'version': 'human-reviewed-v1', 'parent_id': parent_id,
                'analysis_sha256': content_hash(analysis), 'video_sha256': selected_hash,
                'start_frame': first, 'end_frame': last, 'source_words': [w['id'] for w in words], 'text': text}
    candidate = {'id': 'rc_' + content_hash(identity), 'parent_candidate_id': parent_id,
                 'start': start, 'end': end, 'duration_seconds': duration, 'text': text,
                 'source_sentences': [s['id'] for s in sentences], 'source_words': [w['id'] for w in words],
                 'score': None, 'scorer': None, 'scoring_status': 'pending', 'reasons': []}
    # 这不是 analysis 0.1.0 的重新测量结果，而是有人工证据的派生评分视图。
    context = {'kind': 'reviewed_clip_scoring_view', 'time_reference': 'reviewed_clip_start',
               'media': {'normalized_sha256': selected_hash, 'duration_seconds': duration,
                         'has_audio': analysis['media']['has_audio']},
               'words': words, 'sentences': sentences}
    return {'artifact_kind': 'reviewed_candidate', 'version': 'human-reviewed-v1', 'candidate': candidate,
            'provenance': {'task_id': acceptance['task_id'], 'original_media': deepcopy(analysis['media']),
                           'analysis_sha256': content_hash(analysis), 'original_candidates_sha256': content_hash(scored),
                           'acceptance_sha256': content_hash(acceptance), 'render_manifest_sha256': content_hash(render),
                           'rendered_video_sha256': selected_hash, 'input_clip_sha256': input_hash,
                           'start_frame': first, 'end_frame_exclusive': last, 'fps': 30,
                           'timing_adjustments': adjustments},
            'human_review': {'status': 'accepted_by_user', 'feedback': acceptance['user_feedback_verbatim'],
                             'scope': 'single rendered boundary sample; no aggregate completeness claim'},
            'scoring_context': context, 'original_score_reused': False, 'model_requests': 0}


def rescore_rule(record, *, config=None, audio=None):
    result = deepcopy(record)
    config = config or load_config()
    view = result['scoring_context']
    candidate = result['candidate']
    local = {**candidate, 'start': 0.0, 'end': candidate['duration_seconds']}
    candidate.update(RuleScorer(config).score(local, view, audio))
    candidate.update(scorer='rule', scoring_status='scored')
    result['scoring'] = {'version': 'rule-v1', 'config': config.to_dict(),
                         'audio': audio.metadata() if audio else {'status': 'unavailable'},
                         'audio_baseline_scope': 'selected clip only',
                         'meaning': 'new local rule score; not comparable to previous model score or full-video audio baseline'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'candidates', 'acceptance', 'render', 'video', 'input-clip', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--parent-id', required=True)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError('输出已存在，不能覆盖历史结果')
        read = lambda path: json.loads(path.read_text(encoding='utf-8-sig'))
        result = rebuild(read(args.analysis), read(args.candidates), args.parent_id, read(args.acceptance),
                         read(args.render), args.video, args.input_clip)
        audio = measure_audio(args.video, result['scoring_context'], ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
        result = rescore_rule(result, audio=audio)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        parser.exit(1, f'FAIL: {error}\n')
    print(f"PASS: {result['candidate']['id']}; rule score={result['candidate']['score']}; model requests=0")


if __name__ == '__main__':
    main()
