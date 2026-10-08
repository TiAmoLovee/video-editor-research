"""Read-only, source-bound context around a validated candidate's end."""
from bisect import bisect_left

VERSION = 'end-context-v1'


class EndContext:
    """Index validated analysis once; expose observations, never verdicts."""

    def __init__(self, analysis):
        self.sentences = sorted(analysis['sentences'], key=lambda s: (s['start'], s['end'], s['id']))
        self.starts = [s['start'] for s in self.sentences]
        self.by_id = {s['id']: s for s in self.sentences}
        self.speech = analysis['speech']

    @staticmethod
    def excerpt(sentence):
        return {k: sentence[k] for k in ('id', 'start', 'end', 'text')}

    def for_candidate(self, candidate):
        included = sorted((self.by_id[sid] for sid in candidate['source_sentences']),
                          key=lambda s: (s['end'], s['start'], s['id']))
        end = candidate['end']
        following = self.sentences[bisect_left(self.starts, end):]
        following = [s for s in following if s['id'] not in candidate['source_sentences']][:2]
        return {'version': VERSION, 'candidate_end': end,
                'ending_sentences': [self.excerpt(s) for s in included[-2:]],
                'following_sentences': [self.excerpt(s) for s in following],
                'next_sentence_gap_seconds': round(following[0]['start']-end, 9) if following else None,
                'vad_speech_at_end': any(s['start'] <= end < s['end'] for s in self.speech),
                'semantic_completeness': 'not_determined',
                'scope': 'original ASR and VAD observations; following text is outside the candidate; not a boundary verdict'}
