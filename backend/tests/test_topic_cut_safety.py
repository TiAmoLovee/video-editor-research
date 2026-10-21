"""Boundary evidence and coverage safety, independently from quality labels."""

from copy import deepcopy
from dataclasses import asdict
import os
import unittest
from unittest.mock import patch

from test_candidates import fixture
from clipforge.decision.generation import (
    GUARDED_TOPIC, effective_boundary_settings, generate_configured_candidates,
    settings_snapshot,
)
from clipforge.decision.topic_segments import (
    GUARDED_GENERATOR_VERSION as V2, TopicOptions, generate_topic_candidates, segment_topics,
)
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.validation import validate_candidates
from clipforge.analysis.sentences import SENTENCE_PARAMETERS, split_sentences


def corpus(*, gap=0, punctuation=''):
    data = fixture([(i * (8 + gap), i * (8 + gap) + 8) for i in range(8)])
    for i, (word, sentence) in enumerate(zip(data['words'], data['sentences'])):
        word['text'] = sentence['text'] = ('cooking kitchen recipe' if i < 4
                                        else 'astronomy galaxy telescope') + punctuation
    return data


class TopicCutSafetyTests(unittest.TestCase):
    def test_v1_replay_and_v2_reject_continuous_speech_valley(self):
        data = corpus()
        before = deepcopy(data)
        old = generate_topic_candidates(data)
        new = generate_topic_candidates(data, version=V2)
        self.assertEqual(old['candidate_count'], 2)
        self.assertEqual([(c['start'], c['end']) for c in new['candidates']], [(0, 64)])
        self.assertIn('topic_unsupported_cut_rejected', new['warnings'])
        self.assertEqual(data, before)
        validate_candidates(old, data)
        validate_candidates(new, data)

    def test_existing_pause_threshold_or_punctuation_supplies_cut_evidence(self):
        for gap, punctuation in ((.4, ''), (0, '。')):
            with self.subTest(gap=gap, punctuation=punctuation):
                data = corpus(gap=gap, punctuation=punctuation)
                new = generate_topic_candidates(data, version=V2)
                self.assertEqual(new['candidate_count'], 2)
                validate_candidates(new, data)
                report = segment_topics(data, version=V2)
                accepted = [b for b in report['boundaries'] if b['decision'] == 'accepted_valley']
                self.assertTrue(all(b['cut_support']['supported'] for b in accepted))
                self.assertFalse(report['cut_policy']['semantic_completeness_verified'])

    def test_short_pause_does_not_certify_utterance_end(self):
        for gap in (0, .14, .399):
            with self.subTest(gap=gap):
                new = generate_topic_candidates(corpus(gap=gap), version=V2)
                self.assertEqual(new['candidate_count'], 1)

    def test_earlier_ending_overlapping_sentence_cannot_approve_later_cut(self):
        data = fixture([(0, 8), (8, 16), (16, 24), (24, 32), (25, 27),
                        (32, 40), (40, 48), (48, 56), (56, 64)])
        for i, (word, sentence) in enumerate(zip(data['words'], data['sentences'])):
            text = 'cooking kitchen recipe' if i < 5 else 'astronomy galaxy telescope'
            word['text'] = sentence['text'] = text + ('。' if i == 4 else '')
        report = segment_topics(data, version=V2)
        self.assertFalse(report['boundaries'][3]['cut_support']['terminal_punctuation'])
        self.assertEqual(generate_topic_candidates(data, version=V2)['candidate_count'], 1)

    def test_only_webrtc_disagreement_does_not_discard_a_silero_gated_pause(self):
        data = fixture([(0, 20), (30, 50)])
        data['analyzers']['asr']['parameters']['speech_gate'] = {
            'speech_intervals': [{'start': 0, 'end': 20}, {'start': 30, 'end': 50}]}
        new = generate_topic_candidates(data, version=V2)
        self.assertEqual(new['candidate_count'], 2)
        self.assertIn('topic_coverage_detector_disagreement', new['warnings'])
        validate_candidates(new, data)

    def test_suspect_word_gap_is_not_a_safe_hard_pause(self):
        data = fixture([(0, 20), (50, 70), (75, 95)])
        new = generate_topic_candidates(data, version=V2)
        report = segment_topics(data, version=V2)
        self.assertGreater(len(report['word_coverage']['suspect_intervals']), 0)
        self.assertEqual(new['candidate_count'], 0)
        self.assertIn('topic_suspect_asr_coverage', new['warnings'])
        self.assertTrue(all('suspect_asr_coverage' in s['reasons'] for s in report['segments']))
        validate_candidates(new, data)

    def test_actual_silence_still_separates_eligible_topics(self):
        data = fixture([(0, 20), (30, 50)])
        data['speech'] = [{'start': 0, 'end': 20}, {'start': 30, 'end': 50}]
        data['silence'] = [{'start': 20, 'end': 30}, {'start': 50, 'end': 51}]
        new = generate_topic_candidates(data, version=V2)
        self.assertEqual(new['candidate_count'], 2)
        validate_candidates(new, data)

    def test_compound_split_across_long_gap_is_not_semantic_approval(self):
        data = fixture([(0, 10), (10, 20), (50, 60), (60, 70)])
        for word, text in zip(data['words'], ['欢迎', '小伙', '伴', '参加。']):
            word['text'] = text
        data['sentences'] = split_sentences(data['words'])
        data['analyzers']['asr']['parameters']['sentence_rules'] = dict(SENTENCE_PARAMETERS)
        data['speech'] = [{'start': 0, 'end': 20}, {'start': 50, 'end': 70}]
        data['silence'] = [{'start': 20, 'end': 50}, {'start': 70, 'end': 71}]
        report = segment_topics(data, version=V2)
        self.assertEqual(report['blocking_coverage_intervals'], [])
        self.assertTrue(all('unverified_compound_gap' in s['reasons'] for s in report['segments']))
        new = generate_topic_candidates(data, version=V2)
        self.assertEqual(new['candidate_count'], 0)
        self.assertIn('topic_unverified_compound_gap', new['warnings'])
        validate_candidates(new, data)

    def test_rejected_valley_cannot_force_truncation_of_overlong_topic(self):
        data = fixture([(i * 15, (i + 1) * 15) for i in range(8)])
        for i, (word, sentence) in enumerate(zip(data['words'], data['sentences'])):
            word['text'] = sentence['text'] = 'alpha recipe' if i < 4 else 'omega galaxy'
        new = generate_topic_candidates(data, version=V2)
        self.assertEqual(new['candidate_count'], 0)
        self.assertIn('topic_long_segment_skipped', new['warnings'])

    def test_versioned_dispatch_scoring_and_identity(self):
        data = corpus(punctuation='。')
        settings = {'version': GUARDED_TOPIC, 'options': asdict(TopicOptions())}
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': GUARDED_TOPIC}):
            self.assertEqual(settings_snapshot(), settings)
            new = generate_configured_candidates(data)
        self.assertEqual(new['generator'], settings)
        old = generate_topic_candidates(data)
        self.assertTrue(set(c['id'] for c in new['candidates']).isdisjoint(
            c['id'] for c in old['candidates']))
        self.assertEqual(effective_boundary_settings({'mode': 'sentence'}, settings), {'mode': 'off'})
        validate_scored(score_candidates(new, data), data)
        corrupted = deepcopy(new)
        corrupted['generator']['version'] = 'topic-valley-v1'
        with self.assertRaises(ValueError):
            validate_candidates(corrupted, data)

    def test_unresolved_short_terminal_recovery_blocks_only_adjoining_topic(self):
        data = fixture([(0, 20), (25, 45)])
        data['speech'] = [{'start': 0, 'end': 20}, {'start': 25, 'end': 45}]
        data['silence'] = [{'start': 20, 'end': 25}, {'start': 45, 'end': 46}]
        data['analyzers']['asr']['parameters']['coverage_recovery'] = {
            'version': 'local-word-coverage-recovery-v2',
            'remaining_recovery_gaps': [{'start': 45, 'end': 46,
                'reason': 'terminal_speech_beyond_last_word'}]}
        report = segment_topics(data, version=V2)
        self.assertEqual(report['word_coverage']['suspect_interval_count'], 0)
        self.assertIn('suspect_asr_coverage', report['segments'][-1]['reasons'])
        result = generate_topic_candidates(data, version=V2)
        self.assertEqual([(c['start'], c['end']) for c in result['candidates']], [(0, 20)])
        validate_candidates(result, data)
        data['analyzers']['asr']['parameters']['coverage_recovery']['remaining_recovery_gaps'] = []
        self.assertEqual(generate_topic_candidates(data, version=V2)['candidate_count'], 2)


if __name__ == '__main__':
    unittest.main()
