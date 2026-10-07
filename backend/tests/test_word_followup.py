from copy import deepcopy
import unittest

from backend.tests.test_candidates import fixture
from clipforge.analysis.sentences import split_sentences, split_sentences_with_trace
from clipforge.decision.acceptance import checked_entry
from clipforge.decision.boundary_audit import audit_boundaries
from clipforge.decision.candidates import content_hash, generate_candidates
from clipforge.decision.evaluation import QUESTIONS, summary
from clipforge.decision.scoring import score_candidates
from clipforge.decision.word_followup import freeze_word_followup


class WordFollowupTests(unittest.TestCase):
    def setUp(self):
        self.analysis = fixture([(0, 10), (10, 20), (20, 30), (30, 40)])
        self.scored = score_candidates(generate_candidates(self.analysis), self.analysis)
        self.parent = next(c for c in self.scored['candidates'] if c['start']==0 and c['end']==40)
        self.edit = dict(label='synthetic', parent_candidate_id=self.parent['id'],
                         first_word_id='w1', last_word_id='w2', reason='synthetic context proposal')
        self.link = dict(parent_batch_id='a'*64, parent_manifest_sha256='b'*64,
                         parent_review_sha256='c'*64, reason='synthetic regression only')

    def freeze(self, edits=None):
        return freeze_word_followup(self.analysis, self.scored, 'synthetic',
                                    edits if edits is not None else [self.edit], self.link)

    def record(self):
        manifest = self.freeze()
        s = manifest['samples'][0]
        manifest['renders'] = {'S01':dict(file=s['file'], sha256='d'*64,
                                         frames=s['end_frame_exclusive']-s['start_frame'],
                                         duration_seconds=s['duration_seconds'])}
        review = dict(batch_id=manifest['batch_id'], manifest_sha256=content_hash(manifest),
                      revision=1, reviews={'S01':dict(answers=dict.fromkeys(QUESTIONS,'yes'),
                                                       note='', provenance='user_submitted_local_form')})
        return dict(version='human-evaluation-acceptance-v1', task_id='task', sample_id='S01',
                    selected_sha256='d'*64, evaluation=dict(manifest=manifest,human_review=review))

    def test_accepted_word_range_has_no_inherited_score_and_preserves_originals(self):
        before = content_hash([self.analysis,self.scored])
        a = self.record()
        entry = checked_entry('task', self.analysis, self.scored, a)
        self.assertEqual((entry['start'],entry['end']), (10,30))
        self.assertEqual(entry['parent_candidate_id'],self.parent['id'])
        self.assertIsNone(entry['score'])
        self.assertEqual(entry['score_scope'],'unscored_editorial_range')
        self.assertEqual(content_hash([self.analysis,self.scored]),before)
        result = summary(a['evaluation']['manifest'], a['evaluation']['human_review']['reviews'])
        self.assertIsNone(result['boundary_rate'])
        self.assertIsNone(result['pilot_meets_85_percent'])

    def test_no_and_uncertain_never_import(self):
        for answer in ('no','uncertain'):
            a = self.record()
            a['evaluation']['human_review']['reviews']['S01']['answers']['ending_complete'] = answer
            with self.assertRaises(ValueError):
                checked_entry('task',self.analysis,self.scored,a)

    def test_rehashed_tampered_word_plan_still_rejected(self):
        for field,value in [('text','fabricated'),('source_words',['w0']),('original_score',99),
                            ('start_frame',301),('parent_candidate_id','unknown')]:
            a = self.record()
            m = a['evaluation']['manifest']
            m['samples'][0][field] = value
            m['batch_id'] = content_hash({k:v for k,v in m.items() if k not in ('batch_id','renders','prepared_at')})
            a['evaluation']['human_review'].update(batch_id=m['batch_id'],manifest_sha256=content_hash(m))
            with self.assertRaises(ValueError):
                checked_entry('task',self.analysis,self.scored,a)

    def test_unknown_reversed_short_duplicate_and_outside_parent_rejected(self):
        small = next(c for c in self.scored['candidates'] if c['start']==0 and c['end']==20)
        edits = [dict(first_word_id='unknown'),dict(first_word_id='w3'),dict(last_word_id='w1'),
                 dict(parent_candidate_id=small['id'])]
        for change in edits:
            with self.assertRaises(ValueError):
                self.freeze([{**self.edit,**change}])
        with self.assertRaises(ValueError):
            self.freeze([self.edit,self.edit])

    def test_split_trace_preserves_chunk_identity_and_reports_forced_split(self):
        words = [dict(id=f'w{i}',text='学习',start=i*.2,end=(i+1)*.2,probability=.9) for i in range(40)]
        original = deepcopy(words)
        sentences,trace = split_sentences_with_trace(words)
        self.assertEqual(sentences,split_sentences(words))
        self.assertEqual(words,original)
        self.assertIn('limit_without_boundary',[t['reason'] for t in trace])
        self.assertEqual([t['last_word_id'] for t in trace],[s['word_ids'][-1] for s in sentences])

    def test_audit_refuses_to_attribute_a_different_historical_splitter(self):
        with self.assertRaises(ValueError):
            audit_boundaries(self.analysis,self.scored)


if __name__ == '__main__':
    unittest.main()
