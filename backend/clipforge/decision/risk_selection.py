"""Opt-in development policy: prioritize candidates without lexical tail warnings.

No warning is not a semantic pass. Default selection and human labels stay intact.
"""
from clipforge.decision.selection import build_selection, temporal_iou, IOU_THRESHOLD
from clipforge.decision.topics import group_topics

VERSION = 'tail-priority-nms-v1'


def build_risk_selection(scored, analysis, review=None, *, iou_threshold=IOU_THRESHOLD):
    report = build_selection(scored, analysis, review, iou_threshold=iou_threshold)
    notes = {item['candidate_id']: item for item in report['items']}
    ordered = sorted(scored['candidates'], key=lambda c: (
        notes[c['id']]['text_review'] is not None, c['rank'], c['id']))
    kept = []
    for order, candidate in enumerate(ordered, 1):
        item = notes[candidate['id']]
        match = next((c for c in kept if temporal_iou(candidate, c) >= iou_threshold), None)
        item['baseline_retained'] = item['retained']
        item['selection_order'] = order
        item['tail_priority'] = 'review_flagged' if item['text_review'] else 'no_lexical_flag'
        item['retained'] = match is None
        item['suppressed_by'] = match['id'] if match else None
        item['overlap_iou'] = temporal_iou(candidate, match) if match else None
        if match is None:
            kept.append(candidate)
    topics = group_topics(kept)
    for item in report['items']:
        item['topic'] = topics['items'].get(item['candidate_id'])
    report['baseline_selection_version'] = report['version']
    report['version'] = VERSION
    report['config']['priority'] = 'no_lexical_flag_first_then_original_rank'
    report['scope'] = ('Experimental NMS order only; original ranges and scores unchanged; '
                       'no lexical flag is not semantic approval; not independent validation')
    report['topics'] = topics
    report['summary'].update(
        retained_count=len(kept), suppressed_count=len(ordered)-len(kept),
        retained_review_count=sum(i['retained'] and i['boundary_status'] == 'review_required' for i in report['items']),
        retained_text_warning_count=sum(i['retained'] and i['text_review'] is not None for i in report['items']),
        newly_retained_count=sum(i['retained'] and not i['baseline_retained'] for i in report['items']),
        newly_suppressed_count=sum(not i['retained'] and i['baseline_retained'] for i in report['items']))
    report['automatic_acceptance'] = False
    return report
