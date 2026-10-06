"""Task-local, explicit import of human-approved media; no model calls or cache writes."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import shutil

from clipforge.decision.assessment import evaluate_assessment
from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash
from clipforge.decision.scoring import validate_scored


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def checked_entry(task_id, analysis, candidates, acceptance, reviewed=None):
    validate_scored(candidates, analysis)
    a = acceptance
    if a.get('task_id') != task_id or not isinstance(a.get('user_feedback_verbatim'), str) or not a['user_feedback_verbatim'].strip():
        raise ValueError('验收任务或用户反馈无效')
    if a.get('version') == 'human-candidate-acceptance-v1':
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
    tolerance = 1/30+1e-8 if score_scope == 'original_candidate_range' else 1e-8
    if abs(first/30-candidate['start']) > tolerance or abs(last/30-candidate['end']) > tolerance:
        raise ValueError('评分范围与试听范围不匹配')
    digest = a['selected_sha256']
    return {'id': digest, 'candidate_id': candidate['id'], 'parent_candidate_id': parent_id,
            'start': first/30, 'end': last/30, 'duration_seconds': (last-first)/30,
            'text': candidate['text'], 'score': candidate['score'], 'scorer': candidate['scorer'],
            'reasons': deepcopy(candidate['reasons']), 'score_scope': score_scope,
            'score_start': candidate['start'], 'score_end': candidate['end'],
            'status': 'accepted_by_user', 'feedback': a['user_feedback_verbatim'],
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
    else:
        with Path(video).open('rb') as src, media.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    if not record.exists():
        with record.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
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
