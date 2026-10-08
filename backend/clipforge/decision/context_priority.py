"""Opt-in review priority, not a quality score or automatic acceptance."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from clipforge.decision.candidates import content_hash
from clipforge.decision.context_proposals import build_context_proposals
from clipforge.decision.selection import temporal_iou
from clipforge.decision.question_cues import question_context

VERSION = 'context-review-priority-v1'
DEPENDENT_OPENING = re.compile(
    r'^(?:而且|所以|但是|因为|也是|还有|还行|是这样|这个|那个|这种|那种|'
    r'他(?:就是|说|们)|她(?:就是|说|们)|它(?:这个|的|是|们)|'
    r'[\u4e00-\u9fff]{1,5}(?:不出来|不了|不到|不清楚|不明白))')


def rank_context_proposals(analysis, scored, *, max_recommendations=3, exclude_conditional_cues=False):
    if type(max_recommendations) is not int or not 1 <= max_recommendations <= 10:
        raise ValueError('优先查看数量须为 1–10 的整数')
    generated = build_context_proposals(analysis,scored,exclude_conditional_cues=exclude_conditional_cues)
    anchors = {a['word_id']:a for a in generated['anchors']}
    cue_notes = {key:question_context(analysis['words'],a) for key,a in anchors.items()}
    valid_cues = [a for key,a in anchors.items() if cue_notes[key] is None]
    items = []
    for original in generated['proposals']:
        p = deepcopy(original)
        issues = []
        segment_origins = [o for o in p['origins'] if o['kind']=='discourse_segment']
        for o in segment_origins:
            note = cue_notes[o['trigger']]
            if note:
                issues.append(note)
        opening = DEPENDENT_OPENING.match(''.join(p['text'].split()))
        if opening:
            issues.append(dict(code='opening_may_need_prior_context',quote=opening.group(),
                               reason='开头含承接、指代或回答表达，需要核对前文是否必需'))
        # An existing cue inside the original range is also relevant, even if
        # the extension did not ADD it. This does not prove unrelated content.
        internal = [a for a in valid_cues if p['start'] < a['start'] < p['end']]
        if internal:
            issues.append(dict(code='discourse_change_inside',
                               anchors=[dict(word_id=a['word_id'],start=a['start'],quote=a['normalized_quote'])
                                        for a in internal],
                               reason='范围内部含提问或转段提示，需核对是否混入其他话题'))
        cue_problem = any(i['code']=='question_cue_in_condition' for i in issues)
        # Ordinal review buckets, deliberately not 0–100 quality scores.
        tier = 3 if cue_problem else 2 if issues else 0 if segment_origins else 1
        p['context_priority'] = dict(tier=tier,issues=issues,
                                    basis='review order only; absence of signals is not a quality pass')
        items.append(p)
    items.sort(key=lambda p:(p['context_priority']['tier'],len(p['context_priority']['issues']),
                             p['duration_seconds'],p['start'],p['id']))
    selected=[]
    for index,p in enumerate(items,1):
        p['review_rank']=index
        overlap=next((q for q in selected if temporal_iou(p,q)>=.5),None)
        if p['context_priority']['tier']>=2:
            p['queue_status']='deferred_context_risk'
        elif overlap:
            p['queue_status']='deferred_overlap'
        elif len(selected)>=max_recommendations:
            p['queue_status']='deferred_capacity'
        else:
            p['queue_status']='suggested_for_review'
            selected.append(p)
        p['overlap_with']=overlap['id'] if overlap else None
    result = dict(version='context-review-priority-v2' if exclude_conditional_cues else VERSION,
                analysis_sha256=content_hash(analysis),candidates_sha256=content_hash(scored),
                generator_report_sha256=content_hash(generated),items=items,
                recommended_ids=[p['id'] for p in selected],
                config=dict(max_recommendations=max_recommendations,overlap_iou=.5,
                            opening_pattern=DEPENDENT_OPENING.pattern),
                summary=dict(total=len(items),suggested_for_review=len(selected),
                             deferred_context_risk=sum(p['queue_status']=='deferred_context_risk' for p in items),
                             deferred_overlap=sum(p['queue_status']=='deferred_overlap' for p in items),
                             deferred_capacity=sum(p['queue_status']=='deferred_capacity' for p in items)),
                model_requests=0,automatic_acceptance=False,original_selection_changed=False,
                scope='Development heuristics for a small review queue. No human labels read at runtime. '
                      'Deferral is not rejection; all proposals and originals are preserved.')
    if exclude_conditional_cues:
        result['generator_version'] = generated['version']
        result['excluded_anchors'] = deepcopy(generated['excluded_anchors'])
        result['config']['exclude_conditional_cues'] = True
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','candidates','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--exclude-conditional-cues',action='store_true',
                        help='核对 v2 提案风险；不改变默认工作台筛选或人工结论')
    args=parser.parse_args()
    if args.output.exists():
        parser.error('输出必须是新文件')
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    result=rank_context_proposals(read(args.analysis),read(args.candidates),
                                  exclude_conditional_cues=args.exclude_conditional_cues)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['summary']))


if __name__=='__main__':
    main()
