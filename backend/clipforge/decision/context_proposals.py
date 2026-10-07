"""Offline, opt-in context proposals. No human labels, model calls or NMS writes."""
import argparse
import json
import math
from pathlib import Path
import re

from clipforge.analysis.sentences import _units
from clipforge.decision.boundary_audit import audit_boundaries
from clipforge.decision.candidates import content_hash
from clipforge.decision.continuity import tail_signal

VERSION = 'context-proposals-v1'
# Explicit development heuristics, not a learned semantic boundary detector.
ANCHORS = (
    ('question_prompt', r'(?:那)?(?:你觉得|您认为|请问)'),
    ('next_section', r'(?:好的?|那么)?(?:那)?(?:我们)?(?:接下来|下面)(?:来|有|进入|聊|谈|是|要)'),
    ('numbered_question', r'(?:好的?)?第[一二三四五六七八九十0-9]+个问题'),
)


def discourse_anchors(words):
    """Match transcript cues only at existing word AND tokenizer boundaries."""
    units = _units(words)
    allowed = {u['words'][0]['id'] for u in units}
    text, starts, offset = [], {}, 0
    for i, word in enumerate(words):
        part = ''.join(word['text'].split())
        if part and word['id'] in allowed:
            starts[offset] = i
        text.append(part)
        offset += len(part)
    text = ''.join(text)
    found = []
    occupied_until = -1
    matches = sorted((m.start(), -m.end(), kind, m.group())
                     for kind, pattern in ANCHORS for m in re.finditer(pattern, text))
    for start, negative_end, kind, quote in matches:
        if start < occupied_until or start not in starts:
            continue
        i = starts[start]
        found.append(dict(word_index=i, word_id=words[i]['id'], start=words[i]['start'],
                          kind=kind, normalized_quote=quote, semantic_topic_change_verified=False))
        occupied_until = -negative_end
    return found


def build_context_proposals(analysis, scored, *, max_context_seconds=20.0):
    if (isinstance(max_context_seconds, bool) or not isinstance(max_context_seconds, (int,float))
            or not math.isfinite(max_context_seconds) or not 0 < max_context_seconds <= 30):
        raise ValueError('上下文扩展上限须为 (0,30] 秒')
    audit = audit_boundaries(analysis, scored)
    words, candidates = analysis['words'], scored['candidates']
    word_index = {w['id']:i for i,w in enumerate(words)}
    sentences = {s['id']:s for s in analysis['sentences']}
    notes = {i['candidate_id']:i for i in audit['items']}
    units = _units(words)
    allowed_starts = {u['words'][0]['id'] for u in units}
    allowed_ends = {u['words'][-1]['id'] for u in units}
    anchors = discourse_anchors(words)
    proposals, rejected, unique = [], [], {}

    def add(lo, hi, kind, trigger, evidence):
        reason = None
        selected = words[lo:hi+1]
        if not selected:
            return
        start, end = min(w['start'] for w in selected), max(w['end'] for w in selected)
        first, last = math.floor(start*30+1e-8), math.ceil(end*30-1e-8)
        parents = [c for c in candidates if c['start'] <= start < end <= c['end']]
        if selected[0]['id'] not in allowed_starts or selected[-1]['id'] not in allowed_ends:
            reason = 'inside_token'
        elif not 15 <= (last-first)/30 <= 90:
            reason = 'duration_limit'
        elif any(w['start'] < end and w['end'] > start for w in words[:lo]+words[hi+1:]):
            reason = 'overlapping_excluded_word'
        elif any(b['start']-a['end'] > scored['generator']['options']['max_gap_seconds']
                 for a,b in zip(selected,selected[1:])):
            reason = 'long_speech_gap'
        elif not parents:
            reason = 'no_covering_candidate'
        if reason:
            rejected.append(dict(kind=kind, trigger=trigger, start=start, end=end, reason=reason))
            return
        origin = dict(kind=kind,trigger=trigger,evidence=evidence)
        key = (first,last)
        if key in unique:
            unique[key]['origins'].append(origin)
            return
        refs = [w['id'] for w in selected]
        parent = min(parents, key=lambda c:(c['duration_seconds'],c['id']))
        identity = dict(version=VERSION,analysis_sha256=content_hash(analysis),
                        source_words=refs,start_frame=first,end_frame=last)
        item = dict(id='cp_'+content_hash(identity),start=start,end=end,
                    start_frame=first,end_frame_exclusive=last,fps=30,duration_seconds=(last-first)/30,
                    source_words=refs,source_sentences=[s['id'] for s in analysis['sentences']
                                                     if set(refs).intersection(s['word_ids'])],
                    text=''.join(w['text'] for w in selected).strip(),parent_candidate_id=parent['id'],
                    origins=[origin],score=None,scorer=None,status='proposal_not_accepted',
                    semantic_completeness_verified=False)
        unique[key] = item
        proposals.append(item)

    for i, anchor in enumerate(anchors):
        following = anchors[i+1] if i+1<len(anchors) else None
        hi = following['word_index']-1 if following else len(words)-1
        add(anchor['word_index'],hi,'discourse_segment',anchor['word_id'],
            dict(opening=anchor,ending=following or {'kind':'source_speech_end_not_semantic_verdict'}))

    def issues(c):
        note = notes[c['id']]
        return ('opening_at_mechanical_split' in note['issues'],
                'ending_at_mechanical_split' in note['issues']
                or tail_signal(sentences[c['source_sentences'][-1]]['text']) is not None)

    for c in candidates:
        opening, ending = issues(c)
        if not (opening or ending):
            continue
        options = []
        for alternative in candidates:
            if alternative['id'] == c['id'] or any(issues(alternative)):
                continue
            if not alternative['start'] <= c['start'] < c['end'] <= alternative['end']:
                continue
            if (not opening and alternative['start'] != c['start']) or (not ending and alternative['end'] != c['end']):
                continue
            extra = (c['start']-alternative['start'])+(alternative['end']-c['end'])
            if extra > max_context_seconds:
                continue
            # Adding a new discourse cue is a reason to abstain, not evidence to pad further.
            if any(alternative['start'] < a['start'] < c['start']
                   or c['end'] <= a['start'] < alternative['end'] for a in anchors):
                continue
            options.append((extra,alternative['id'],alternative))
        if not options:
            rejected.append(dict(kind='context_expansion',trigger=c['id'],reason='no_bounded_context_option'))
            continue
        alternative = min(options,key=lambda x:(x[0],x[1]))[2]
        lo = word_index[sentences[alternative['source_sentences'][0]]['word_ids'][0]]
        hi = word_index[sentences[alternative['source_sentences'][-1]]['word_ids'][-1]]
        add(lo,hi,'context_expansion',c['id'],dict(original_start=c['start'],original_end=c['end'],
            opening_context=notes[c['id']]['opening_context'],ending_context=notes[c['id']]['ending_context'],
            existing_alternative_id=alternative['id']))
    return dict(version=VERSION,analysis_sha256=content_hash(analysis),candidates_sha256=content_hash(scored),
                config=dict(max_context_seconds=max_context_seconds,anchors=[list(a) for a in ANCHORS]),
                anchors=anchors,proposals=proposals,abstentions=rejected,
                summary=dict(proposal_count=len(proposals),anchor_count=len(anchors),abstention_count=len(rejected)),
                automatic_acceptance=False,default_selection_changed=False,model_requests=0,
                scope='Label-free runtime proposal generation; heuristics developed using prior failures. '
                      'Not a semantic verdict, automatic replacement, or independent quality evaluation.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis','candidates','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出必须是新文件，不覆盖历史记录')
    read = lambda p: json.loads(p.read_text(encoding='utf-8-sig'))
    result = build_context_proposals(read(args.analysis),read(args.candidates))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
