"""只读边界修正草案及时间 NMS；不复用旧分数给新范围，不调用模型。"""

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path

from clipforge.decision.candidates import content_hash
from clipforge.decision.scoring import validate_scored
from clipforge.decision.continuity import review_continuity
from clipforge.decision.topics import group_topics

VERSION = 'boundary-nms-v2'
IOU_THRESHOLD = 0.5
EDGE_SECONDS = 3.0
WORD_SNAP_SECONDS = 0.15


def validate_review(review, analysis):
    if review is None:
        return []
    if (not isinstance(review, dict) or set(review) != {'version', 'source_sha256', 'normalized_sha256', 'cuts'}
            or review['version'] != 'human-scene-review-v1'
            or any(review[k] != analysis['media'][k] for k in ('source_sha256', 'normalized_sha256'))
            or not isinstance(review['cuts'], list) or len(review['cuts']) > 1000):
        raise ValueError('人工边界记录与当前媒体不匹配')
    cuts = []
    for item in review['cuts']:
        if (not isinstance(item, dict) or set(item) != {'frame', 'fps', 'topic_change', 'note'}
                or type(item['frame']) is not int or item['frame'] <= 0
                or type(item['fps']) is not int or item['fps'] != 30
                or type(item['topic_change']) is not bool
                or not isinstance(item['note'], str) or not 1 <= len(item['note']) <= 1000):
            raise ValueError('人工边界记录格式错误')
        seconds = item['frame'] / item['fps']
        if seconds >= analysis['media']['duration_seconds'] or seconds in [x['seconds'] for x in cuts]:
            raise ValueError('人工边界越界或重复')
        cuts.append({'seconds': seconds, 'source': 'human_review', 'topic_change': item['topic_change']})
    return cuts


def temporal_iou(a, b):
    intersection = max(0, min(a['end'], b['end']) - max(a['start'], b['start']))
    union = max(a['end'], b['end']) - min(a['start'], b['start'])
    return intersection / union if union > 0 else 0.0


def boundary_check(candidate, analysis, cuts):
    start, end = candidate['start'], candidate['end']
    issues, evidence, proposal = [], [], None
    finding = candidate.get('llm_assessment', {}).get('completeness', {}).get('finding')
    if finding in ('incomplete', 'needs_context', 'uncertain'):
        issues.append('model_boundary_uncertain')
    for cut in cuts:
        t = cut['seconds']
        if not start < t < end:
            continue
        near_start, near_end = t - start <= EDGE_SECONDS, end - t <= EDGE_SECONDS
        if not cut['topic_change'] and not (near_start or near_end):
            continue
        issues.append('topic_change_inside' if cut['topic_change'] else 'scene_change_near_edge')
        crossing = [w for w in analysis['words'] if w['start'] < t < w['end']]
        evidence.append({**cut, 'crossing_word_ids': [w['id'] for w in crossing]})
        if crossing:
            issues.append('word_crosses_visual_cut')
        # 一个镜头切换不自动证明话题结束。这里只生成待核对的范围，永不写回候选。
        if proposal is not None or not (near_start or near_end):
            continue
        side = 'end' if near_end and (not near_start or end - t <= t - start) else 'start'
        if crossing:
            target = max(w['end'] for w in crossing)
            if target - t > WORD_SNAP_SECONDS:
                issues.append('no_nearby_word_safe_boundary')
                continue
        else:
            target = t
        new_start, new_end = (start, target) if side == 'end' else (target, end)
        if any(w['start'] < target < w['end'] for w in analysis['words']):
            issues.append('no_nearby_word_safe_boundary')
            continue
        duration = round(new_end - new_start, 9)
        if not 15 <= duration <= 90:
            issues.append('proposal_outside_duration_limits')
            continue
        words = [w for w in analysis['words'] if w['start'] >= new_start and w['end'] <= new_end]
        if not words:
            continue
        refs = {w['id'] for w in words}
        proposal = {
            'start': new_start, 'end': new_end, 'duration_seconds': duration,
            'text': ''.join(w['text'] for w in words),
            'source_words': [w['id'] for w in words],
            'source_sentences': [s['id'] for s in analysis['sentences'] if refs.intersection(s['word_ids'])],
            'visual_cut_seconds': t, 'word_boundary_shift_seconds': round(target - t, 9),
            'score': None, 'status': 'review_then_rescore',
            'semantic_completeness_verified': False,
        }
    return {'boundary_status': 'review_required' if issues else 'not_verified',
            'issues': sorted(set(issues)), 'cut_evidence': evidence, 'proposal': proposal}


def build_selection(candidates, analysis, review=None, *, iou_threshold=IOU_THRESHOLD):
    if (isinstance(iou_threshold, bool) or not isinstance(iou_threshold, (float, int))
            or not math.isfinite(iou_threshold) or not 0 < iou_threshold <= 1):
        raise ValueError('NMS 阈值必须在 (0, 1]')
    validate_scored(candidates, analysis)
    text_review = review_continuity(candidates, analysis)
    text_notes = {i['candidate_id']: i for i in text_review['items']}
    reviewed = validate_review(review, analysis)
    by_time = {s['start']: {'seconds': s['start'], 'source': 'shot_detector', 'topic_change': False}
               for s in analysis['shots'][1:]}
    by_time.update({c['seconds']: c for c in reviewed})
    cuts = [by_time[t] for t in sorted(by_time)]
    kept, items = [], []
    for candidate in candidates['candidates']:
        boundary = boundary_check(candidate, analysis, cuts)
        text_note = text_notes.get(candidate['id'])
        if text_note:
            boundary['boundary_status'] = 'review_required'
            boundary['issues'].append(text_note['signal']['code'])
        boundary['text_review'] = text_note
        match = next((other for other in kept if temporal_iou(candidate, other) >= iou_threshold), None)
        if match is None:
            kept.append(candidate)
        items.append({'candidate_id': candidate['id'], 'original_rank': candidate['rank'],
                      'retained': match is None,
                      'suppressed_by': match['id'] if match else None,
                      'overlap_iou': temporal_iou(candidate, match) if match else None,
                      **boundary})
    topics = group_topics(kept)
    for item in items:
        item['topic'] = topics['items'].get(item['candidate_id'])
    return {'version': VERSION, 'candidates_sha256': content_hash(candidates),
            'analysis_sha256': content_hash(analysis), 'review': deepcopy(review),
            'config': {'iou_threshold': iou_threshold, 'edge_seconds': EDGE_SECONDS,
                       'word_snap_seconds': WORD_SNAP_SECONDS},
            'scope': 'NMS on original scored ranges; scene proposals unscored; text extensions reference existing scores',
            'text_review_version': text_review['version'],
            'summary': {'original_count': len(items), 'retained_count': len(kept),
                        'suppressed_count': len(items) - len(kept),
                        'retained_review_count': sum(i['retained'] and i['boundary_status'] == 'review_required' for i in items),
                        'proposal_count': sum(i['proposal'] is not None for i in items)},
            'topics': topics, 'items': items, 'model_requests': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--review', type=Path, help='明确人工确认的镜头记录；绑定原片及归一化哈希')
    args = parser.parse_args()
    try:
        from clipforge.storage.jobs import get_job, job_dir
        job = get_job(args.task_id)
        if not job or job['status'] != 'SUCCEEDED':
            raise ValueError('只能检查已成功的任务')
        folder = job_dir(args.task_id)
        analysis = json.loads((folder / 'analysis.json').read_text(encoding='utf-8'))
        candidates = json.loads((folder / 'candidates.json').read_text(encoding='utf-8'))
        review_path = folder / 'boundary_review.json'
        review = json.loads(args.review.read_text(encoding='utf-8-sig')) if args.review else (
            json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else None)
        result = build_selection(candidates, analysis, review)
        if args.review:
            # 不覆盖其他人工记录。重复运行同一记录是幂等的。
            if review_path.exists():
                if json.loads(review_path.read_text(encoding='utf-8')) != review:
                    raise ValueError('已有不同人工记录，保留原文件，拒绝覆盖')
            else:
                with review_path.open('x', encoding='utf-8') as stream:
                    stream.write(json.dumps(review, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': 'ready', **result['summary'], 'model_requests': 0}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'FAIL: {error}\n')


if __name__ == '__main__':
    main()
