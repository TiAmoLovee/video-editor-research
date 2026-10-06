"""Conservative text-tail review signals, separate from scores and human acceptance."""
from copy import deepcopy
import re

from clipforge.decision.candidates import content_hash
from clipforge.decision.scoring import validate_scored

VERSION = 'text-tail-review-v1'
# These are review signals, not a parser or proof of semantic incompleteness.
CONDITIONAL = re.compile(r'(?:如果|假如|倘若|要是)[^，,。.!！?？;；:\n：]{1,24}$')
CONSEQUENCE = re.compile(r'就|那么|否则|则|便|会|能|可以|应该|该|吗|呢')
DANGLING = re.compile(r'(?:你就|它就|他就|我们就|他们就|所以|但是|而且|以及|比如|例如)$')


def tail_signal(text):
    """Return an exact quote when a narrow unfinished-tail pattern matches."""
    tail = text.rstrip()
    # Questions and quotations are ambiguous; do not classify them with this rule.
    if not tail or tail[-1] in '?？' or any(ch in tail for ch in '“”「」『』"'):
        return None
    tail = tail.rstrip('，,。.!！;；:： ')
    match = CONDITIONAL.search(tail)
    if match and not CONSEQUENCE.search(match.group()):
        return {'code': 'conditional_tail_needs_review', 'quote': match.group(),
                'reason': '末尾只有疑似条件从句，需核对后文是否还有结果或解释'}
    match = DANGLING.search(tail)
    if match:
        return {'code': 'connective_tail_needs_review', 'quote': match.group(),
                'reason': '末尾为疑似未接完的连接表达，需核对下一句'}
    return None


def review_continuity(scored, analysis):
    """Find risky tails and a shortest existing extension for manual inspection.

    Every suggestion is an already validated/scored candidate. No time, score,
    transcript, NMS or acceptance is changed; absence of a signal is not a pass.
    """
    validate_scored(scored, analysis)
    sentences = {s['id']: s for s in analysis['sentences']}
    signals = {c['id']: tail_signal(sentences[c['source_sentences'][-1]]['text'])
               for c in scored['candidates']}
    items = []
    for candidate in scored['candidates']:
        signal = signals[candidate['id']]
        if signal is None:
            continue
        sources = candidate['source_sentences']
        options = [c for c in scored['candidates']
                   if c['start'] == candidate['start'] and c['end'] > candidate['end']
                   and c['source_sentences'][:len(sources)] == sources
                   and signals[c['id']] is None
                   and all(0 <= sentences[right]['start'] - sentences[left]['end'] <= 3
                           for left, right in zip(c['source_sentences'], c['source_sentences'][1:]))]
        extension = min(options, key=lambda c: (c['end'], c['id'])) if options else None
        items.append({'candidate_id': candidate['id'], 'original_rank': candidate['rank'],
                      'signal': signal, 'status': 'review_required',
                      'existing_extension': ({k: deepcopy(extension[k]) for k in
                                             ('id', 'start', 'end', 'duration_seconds', 'text',
                                              'source_sentences', 'score', 'rank', 'scorer')}
                                             if extension else None),
                      'extension_status': 'needs_audition' if extension else 'no_existing_extension'})
    return {'version': VERSION, 'analysis_sha256': content_hash(analysis),
            'candidates_sha256': content_hash(scored), 'items': items,
            'summary': {'candidate_count': len(scored['candidates']),
                        'flagged_count': len(items),
                        'existing_extension_count': sum(i['existing_extension'] is not None for i in items)},
            'config': {'max_conditional_tail_chars': 24, 'max_gap_seconds': 3},
            'scope': 'lexical review signals only; no signal is not a semantic pass; extensions need human audition',
            'model_requests': 0, 'original_scores_changed': False}
