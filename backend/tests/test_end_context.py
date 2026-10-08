from copy import deepcopy
import unittest

from backend.tests.test_candidates import fixture
from clipforge.decision.candidates import generate_candidates, content_hash
from clipforge.decision.scoring import score_candidates
from clipforge.decision.selection import build_selection
from clipforge.decision.context import EndContext


class EndContextTests(unittest.TestCase):
    def test_following_text_is_source_bound_and_not_part_of_candidate(self):
        analysis = fixture([(0, 10), (10.5, 20), (20.62, 25), (26, 32), (33, 40)])
        scored = score_candidates(generate_candidates(analysis), analysis)
        candidate = next(c for c in scored['candidates'] if c['start'] == 0 and c['end'] == 20)
        before = content_hash([analysis, scored])
        context = EndContext(analysis).for_candidate(candidate)
        self.assertEqual([s['id'] for s in context['ending_sentences']], ['s0', 's1'])
        self.assertEqual([s['id'] for s in context['following_sentences']], ['s2', 's3'])
        self.assertEqual(context['next_sentence_gap_seconds'], .62)
        self.assertEqual(context['following_sentences'][0]['text'], analysis['sentences'][2]['text'])
        self.assertEqual(context['semantic_completeness'], 'not_determined')
        self.assertEqual(before, content_hash([analysis, scored]))

    def test_last_sentence_has_no_invented_following_text(self):
        analysis = fixture([(0, 20)])
        candidate = generate_candidates(analysis)['candidates'][0]
        context = EndContext(analysis).for_candidate(candidate)
        self.assertEqual(context['following_sentences'], [])
        self.assertIsNone(context['next_sentence_gap_seconds'])

    def test_speech_at_cut_does_not_imply_incompleteness_and_end_is_exclusive(self):
        analysis = fixture([(0, 20), (22, 30)])
        candidate = next(c for c in generate_candidates(analysis)['candidates'] if c['end'] == 20)
        for speech, expected in [([{'start': 19, 'end': 21}], True),
                                 ([{'start': 19, 'end': 20}], False), ([], False)]:
            analysis['speech'] = speech
            context = EndContext(analysis).for_candidate(candidate)
            self.assertEqual(context['vad_speech_at_end'], expected)
            self.assertEqual(context['semantic_completeness'], 'not_determined')

    def test_nested_overlap_uses_candidate_end_not_last_sentence_start(self):
        analysis = fixture([(0, 30), (10, 15), (20, 25), (30, 45)])
        candidate = next(c for c in generate_candidates(analysis)['candidates'] if c['end'] == 30)
        context = EndContext(analysis).for_candidate(candidate)
        self.assertEqual(context['ending_sentences'][-1]['id'], 's0')
        self.assertEqual(context['following_sentences'][0]['id'], 's3')
        self.assertEqual(context['next_sentence_gap_seconds'], 0)

    def test_large_gap_is_reported_without_joining_or_changing_selection(self):
        analysis = fixture([(0, 20), (40, 60)])
        scored = score_candidates(generate_candidates(analysis), analysis)
        before = deepcopy(scored)
        report = build_selection(scored, analysis)
        candidate = next(c for c in scored['candidates'] if c['end'] == 20)
        item = next(i for i in report['items'] if i['candidate_id'] == candidate['id'])
        self.assertEqual(item['end_context']['next_sentence_gap_seconds'], 20)
        self.assertTrue(item['retained'])
        self.assertEqual(scored, before)
