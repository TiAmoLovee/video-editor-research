"""Source-bound split provenance and context, without changing human labels."""
from clipforge.analysis.sentences import split_sentences_with_trace
from clipforge.decision.candidates import content_hash
from clipforge.decision.scoring import validate_scored

VERSION = 'split-provenance-audit-v1'


def audit_boundaries(analysis, scored):
    validate_scored(scored, analysis)
    sentences, trace = split_sentences_with_trace(analysis['words'])
    if sentences != analysis['sentences']:
        raise ValueError('当前分块规则不能精确重现来源，不能推断历史切分原因')
    edges = []
    for left, right, reason in zip(sentences, sentences[1:], trace):
        edges.append(dict(left_sentence_id=left['id'], right_sentence_id=right['id'],
                          end=left['end'], next_start=right['start'],
                          gap_seconds=round(right['start']-left['end'], 9),
                          reason=reason['reason'], left_text=left['text'], right_text=right['text'],
                          mechanical_split=reason['reason'] == 'limit_without_boundary',
                          semantic_completeness_verified=False))
    before = {e['right_sentence_id']:e for e in edges}
    after = {e['left_sentence_id']:e for e in edges}
    notes = []
    for c in scored['candidates']:
        opening = before.get(c['source_sentences'][0])
        ending = after.get(c['source_sentences'][-1])
        issues = [side+'_at_mechanical_split' for side, edge in [('opening',opening),('ending',ending)]
                  if edge and edge['mechanical_split']]
        notes.append(dict(candidate_id=c['id'], issues=issues, opening_context=opening,
                          ending_context=ending, status='review_required' if issues else 'not_verified'))
    return dict(version=VERSION, analysis_sha256=content_hash(analysis),
                candidates_sha256=content_hash(scored), exact_replay=True,
                trace=trace, edges=edges, items=notes, automatic_acceptance=False,
                scope='Mechanical split is a review signal, not proof of incompleteness. '
                      'Pauses do not prove completeness; topic changes inside a chunk need context review.')
