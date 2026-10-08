"""Task-local, explicit import of human-approved media; no model calls or cache writes."""
import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from clipforge.decision.assessment import evaluate_assessment
from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash
from clipforge.decision.scoring import validate_scored


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def evaluation_entry(analysis, candidates, acceptance):
    """Read structured answers directly; do not invent a verbatim user quotation."""
    from clipforge.decision.evaluation import summary, QUESTIONS
    exported = acceptance['evaluation']
    manifest, review = exported['manifest'], exported['human_review']
    # Validate the frozen artifact, not today's NMS implementation. Future selector
    # upgrades must not invalidate human approval of unchanged exact media.
    plan = {k: v for k, v in manifest.items() if k not in ('batch_id', 'renders', 'prepared_at')}
    if (manifest['version'] != 'human-evaluation-batch-v1'
            or manifest['cohort'] not in ('editorial_followup_not_baseline', 'all_time_nms_survivors_before_topic_folding')
            or content_hash(plan) != manifest['batch_id']
            or manifest['analysis_sha256'] != content_hash(analysis)
            or manifest['candidates_sha256'] != content_hash(candidates)
            or any(manifest[k] != analysis['media'][k] for k in ('source_sha256', 'normalized_sha256'))
            or len({s['id'] for s in manifest['samples']}) != len(manifest['samples'])
            or len(manifest['samples']) != manifest['denominator']
            or review['batch_id'] != manifest['batch_id']
            or review['manifest_sha256'] != content_hash(manifest)
            or type(review['revision']) is not int or review['revision'] < 1):
        raise ValueError('表单评审与冻结的候选范围或来源不匹配')
    summary(manifest, review['reviews'])
    sid = acceptance['sample_id']
    sample = next((s for s in manifest['samples'] if s['id'] == sid), None)
    answer = review['reviews'].get(sid)
    if (sample is None or not answer or answer['answers'] != dict.fromkeys(QUESTIONS, 'yes')
            or answer.get('provenance') != 'user_submitted_local_form'):
        raise ValueError('只有用户四项明确通过的具体版本可以收录')
    render = manifest['renders'][sid]
    if (render['file'] != sample['file'] or render['sha256'] != acceptance['selected_sha256']
            or render['frames'] != sample['end_frame_exclusive']-sample['start_frame']
            or render['duration_seconds'] != sample['duration_seconds']):
        raise ValueError('实际试听版本与表单记录不匹配')
    word_followup = manifest['selection_version'] == 'editorial-word-boundary-v1'
    if word_followup:
        from clipforge.decision.word_followup import freeze_word_followup
        expected = freeze_word_followup(analysis, candidates, manifest['source_name'],
                                        manifest['word_edits'], manifest['followup_of'])
        if expected != {'batch_id': manifest['batch_id'], **plan}:
            raise ValueError('词语修正计划与原始来源不符')
        candidate = dict(id=sample['candidate_id'], parent_candidate_id=sample['parent_candidate_id'],
                         start=sample['original_start'], end=sample['original_end'],
                         text=sample['text'], source_sentences=sample['source_sentences'],
                         score=None, rank=None, scorer=None, reasons=[])
    else:
        candidate = next((c for c in candidates['candidates'] if c['id'] == sample['candidate_id']), None)
    fields = {'original_start':'start', 'original_end':'end', 'original_score':'score', 'original_rank':'rank',
              'scorer':'scorer', 'text':'text', 'source_sentences':'source_sentences'}
    if (candidate is None or any(sample[k] != candidate[v] for k, v in fields.items())
            or sample['start_frame'] != math.floor(candidate['start']*30+1e-8)
            or sample['end_frame_exclusive'] != math.ceil(candidate['end']*30-1e-8)):
        raise ValueError('表单中的候选文字、评分或范围与原始记录不符')
    timing = {k: sample[k] for k in ('start_frame', 'end_frame_exclusive', 'fps', 'duration_seconds')}
    timing.update(start_seconds=sample['start_frame']/30, end_seconds_exclusive=sample['end_frame_exclusive']/30)
    feedback = '表单评审：开头完整、结尾完整、能独立理解、无无关内容，四项均选择“是”。'
    if answer['note']:
        feedback += ' 用户备注：' + answer['note']
    provenance = {'kind': 'structured_form', 'batch_id': manifest['batch_id'], 'sample_id': sid,
                  'revision': review['revision'], 'manifest_sha256': content_hash(manifest),
                  'review_sha256': content_hash(review), 'cohort': manifest['cohort']}
    return candidate, timing, feedback, provenance


def checked_entry(task_id, analysis, candidates, acceptance, reviewed=None):
    validate_scored(candidates, analysis)
    a = acceptance
    if a.get('task_id') != task_id:
        raise ValueError('验收任务或用户反馈无效')
    feedback = a.get('user_feedback_verbatim')
    provenance = None
    if a.get('version') != 'human-evaluation-acceptance-v1' and (not isinstance(feedback, str) or not feedback.strip()):
        raise ValueError('验收任务或用户反馈无效')
    if a.get('version') == 'human-evaluation-acceptance-v1':
        candidate, timing, feedback, provenance = evaluation_entry(analysis, candidates, a)
        first, last = timing['start_frame'], timing['end_frame_exclusive']
        parent_id = candidate.get('parent_candidate_id', candidate['id'])
        score_scope = 'unscored_editorial_range' if candidate['id'].startswith('wc_') else 'original_candidate_range'
    elif a.get('version') == 'human-candidate-acceptance-v1':
        if any(type(value) is not bool for value in a.get('checks', {}).values()):
            raise ValueError('验收检查项必须是明确的布尔结论')
        if (a.get('status') != 'accepted_by_user' or a.get('checks') != {
                'opening_understandable': True, 'ending_complete': True, 'unrelated_content_included': False}
                or a.get('analysis_sha256') != content_hash(analysis)
                or a.get('original_candidates_sha256') != content_hash(candidates)
                or any(a.get(k) != analysis['media'][k] for k in ('source_sha256', 'normalized_sha256'))):
            raise ValueError('验收结论或来源不匹配')
        candidate = next((c for c in candidates['candidates'] if c['id'] == a.get('candidate_id')), None)
        if candidate is None or a.get('original_candidate_range') != {'start': candidate['start'], 'end': candidate['end']}:
            raise ValueError('验收候选或范围不匹配')
        timing = a['rendered_range']
        first, last = timing['start_frame'], timing['end_frame_exclusive']
        parent_id = candidate['id']
        score_scope = 'original_candidate_range'
    elif a.get('version') == 'human-audition-acceptance-v1' and reviewed is not None:
        if a.get('rendered_boundary_review') != 'accepted_by_user':
            raise ValueError('试听尚未通过')
        p = reviewed['provenance']
        if (reviewed.get('version') != 'human-reviewed-v1' or p['task_id'] != task_id
                or p['analysis_sha256'] != content_hash(analysis)
                or p['original_candidates_sha256'] != content_hash(candidates)
                or p['acceptance_sha256'] != content_hash(a)
                or p['original_media'] != analysis['media']
                or p['rendered_video_sha256'] != a['selected_sha256']):
            raise ValueError('修正版来源不匹配')
        candidate = reviewed['candidate']
        parent_id = candidate['parent_candidate_id']
        parent = next((c for c in candidates['candidates'] if c['id'] == parent_id), None)
        if parent is None or not parent['start'] <= candidate['start'] < candidate['end'] <= parent['end']:
            raise ValueError('修正版不属于原候选')
        timing = a
        first, last = timing['start_frame'], timing['end_frame_exclusive']
        words = [w for w in analysis['words'] if w['start'] < last/30 and w['end'] > first/30]
        refs = {w['id'] for w in words}
        sentences = [s for s in analysis['sentences'] if refs.intersection(s['word_ids'])]
        word_map = {w['id']: w for w in words}
        expected_text = '\n'.join(''.join(word_map[wid]['text'] for wid in s['word_ids'] if wid in refs) for s in sentences)
        identity = {'version': 'human-reviewed-v1', 'parent_id': parent_id,
                    'analysis_sha256': content_hash(analysis), 'video_sha256': a['selected_sha256'],
                    'start_frame': first, 'end_frame': last, 'source_words': [w['id'] for w in words], 'text': expected_text}
        if (candidate['id'] != 'rc_' + content_hash(identity) or candidate['text'] != expected_text
                or candidate['source_words'] != [w['id'] for w in words]
                or candidate['source_sentences'] != [s['id'] for s in sentences]):
            raise ValueError('修正版文字或身份无效')
        if candidate.get('scorer') != 'llm' or reviewed.get('model_scoring', {}).get('status') != 'passed':
            raise ValueError('此导入格式需要已校验的模型重评分')
        assessed = evaluate_assessment(candidate['llm_assessment'], candidate['text'])
        if candidate['score'] != assessed['score'] or candidate['reasons'] != assessed['reasons']:
            raise ValueError('修正版评分无效')
        score_scope = 'rendered_range'
    else:
        raise ValueError('不支持的人工验收记录')
    if (type(first) is not int or type(last) is not int or timing.get('fps') != 30
            or not 0 <= first < last <= math.ceil(analysis['media']['duration_seconds']*30)
            or not 15 <= (last-first)/30 <= 90
            or not re.fullmatch('[a-f0-9]{64}', a['selected_sha256'])):
        raise ValueError('试听帧范围或文件身份无效')
    for name, value in [('start_seconds', first/30), ('end_seconds_exclusive', last/30), ('duration_seconds', (last-first)/30)]:
        if type(timing.get(name)) not in (float, int) or not math.isclose(timing[name], value, abs_tol=1e-8, rel_tol=0):
            raise ValueError('试听秒数与帧数不符')
    tolerance = 1/30+1e-8 if score_scope in ('original_candidate_range', 'unscored_editorial_range') else 1e-8
    if abs(first/30-candidate['start']) > tolerance or abs(last/30-candidate['end']) > tolerance:
        raise ValueError('评分范围与试听范围不匹配')
    digest = a['selected_sha256']
    return {'id': digest, 'candidate_id': candidate['id'], 'parent_candidate_id': parent_id,
            'start': first/30, 'end': last/30, 'duration_seconds': (last-first)/30,
            'text': candidate['text'], 'score': candidate['score'], 'scorer': candidate['scorer'],
            'reasons': deepcopy(candidate['reasons']), 'score_scope': score_scope,
            'score_start': candidate['start'], 'score_end': candidate['end'],
            'status': 'accepted_by_user', 'feedback': feedback, 'review_provenance': provenance,
            'video_url': f'/tasks/{task_id}/accepted/{digest}.mp4'}


def load_accepted(folder, task_id, analysis, candidates):
    root = Path(folder).resolve()
    directory = root/'accepted'
    if not directory.exists():
        return []
    if not directory.resolve().is_relative_to(root):
        raise ValueError('验收目录越界')
    items = []
    for path in sorted(directory.glob('*.json')):
        if not re.fullmatch('[a-f0-9]{64}', path.stem) or not path.resolve().is_relative_to(root):
            raise ValueError('验收文件路径无效')
        raw = read(path)
        entry = checked_entry(task_id, analysis, candidates, raw['acceptance'], raw.get('reviewed'))
        media = directory/f'{entry["id"]}.mp4'
        if (entry['id'] != path.stem or not media.resolve().is_relative_to(root)
                or file_hash(media) != entry['id']):
            raise ValueError('验收媒体缺失或发生变化')
        items.append(entry)
    return items


def install(folder, task_id, analysis, candidates, acceptance, video, reviewed=None):
    entry = checked_entry(task_id, analysis, candidates, acceptance, reviewed)
    if file_hash(video) != entry['id']:
        raise ValueError('实际视频与人工试听文件不一致')
    root = Path(folder).resolve()
    directory = root/'accepted'
    if not directory.resolve().is_relative_to(root):
        raise ValueError('验收目录越界')
    directory.mkdir(exist_ok=True)
    payload = {'acceptance': acceptance, 'reviewed': reviewed}
    record, media = directory/f'{entry["id"]}.json', directory/f'{entry["id"]}.mp4'
    if any(not p.resolve().is_relative_to(root) for p in (record, media)):
        raise ValueError('验收文件越界')
    if record.exists() and read(record) != payload:
        raise ValueError('已存在不同验收记录，拒绝覆盖')
    if media.exists():
        if file_hash(media) != entry['id']:
            raise ValueError('已存在不同试听文件，拒绝覆盖')
    # Readers only enumerate final JSON records. Stage complete files on the same
    # filesystem, then publish media first and the record last without overwriting.
    # A failed record publication can leave valid media, which a retry may reuse.
    with tempfile.TemporaryDirectory(prefix='.import-', dir=directory) as temporary:
        staged = Path(temporary)
        if not media.exists():
            with Path(video).open('rb') as src, (staged/'media').open('xb') as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            if file_hash(staged/'media') != entry['id']:
                raise ValueError('复制期间试听文件发生变化，未发布验收记录')
        with (staged/'record').open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        if (staged/'media').exists():
            try:
                os.link(staged/'media', media)
            except FileExistsError:
                if file_hash(media) != entry['id']:
                    raise ValueError('已存在不同试听文件，拒绝覆盖') from None
        try:
            os.link(staged/'record', record)
        except FileExistsError:
            if read(record) != payload:
                raise ValueError('已存在不同验收记录，拒绝覆盖') from None
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--reviewed-score', type=Path)
    args = parser.parse_args()
    from clipforge.storage.jobs import get_job, job_dir
    try:
        job = get_job(args.task_id)
        if not job or job['status'] != 'SUCCEEDED':
            raise ValueError('仅可导入已成功任务')
        folder = job_dir(args.task_id)
        item = install(folder, args.task_id, read(folder/'analysis.json'), read(folder/'candidates.json'),
                       read(args.acceptance), args.video, read(args.reviewed_score) if args.reviewed_score else None)
        print(json.dumps({'status': 'accepted', 'id': item['id'], 'model_requests': 0}))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'FAIL: {error}\n')


if __name__ == '__main__':
    main()
