"""Topic boundaries precede selection: no arbitrary sentence-window fallback."""

from copy import deepcopy
from dataclasses import asdict, replace
import os
import unittest
from unittest.mock import patch

from test_candidates import fixture
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.generation import (
    LEGACY, TOPIC, effective_boundary_settings, generate_configured_candidates,
    settings_snapshot, validate_settings,
)
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.topic_segments import TopicOptions, generate_topic_candidates, segment_topics
from clipforge.decision.validation import validate_candidates


def text_analysis(texts, seconds=8):
    data = fixture([(i * seconds, (i + 1) * seconds) for i in range(len(texts))])
    for word, sentence, text in zip(data['words'], data['sentences'], texts):
        word['text'] = sentence['text'] = text
    return data


class TopicGenerationTests(unittest.TestCase):
    def case(self, texts, seconds=8, options=None):
        analysis = text_analysis(texts, seconds)
        result = generate_topic_candidates(analysis, options)
        validate_candidates(result, analysis)
        return analysis, result, segment_topics(analysis, options)

    def test_distinct_topics_generate_whole_topics_not_overlapping_subwindows(self):
        analysis, result, report = self.case(
            ['��������߲���������ʳ��'] * 4 + ['���Ĺ۲����Ӻ���������Զ��'] * 4)
        ranges = [(c['start'], c['end']) for c in result['candidates']]
        self.assertEqual(ranges, [(0, 32), (32, 64)])
        self.assertLess(result['candidate_count'], generate_candidates(analysis)['candidate_count'])
        self.assertEqual([c['source_sentences'] for c in result['candidates']],
                         [[f's{i}' for i in range(4)], [f's{i}' for i in range(4, 8)]])
        self.assertEqual(report['model_requests'], 0)

    def test_uniform_topic_kept_as_one_range(self):
        _, result, _ = self.case(['�����Ӱ��Ȧ���ž�ͷ�ع�'] * 7)
        self.assertEqual([(c['start'], c['end']) for c in result['candidates']], [(0, 56)])

    def test_short_topic_not_padded_across_hard_pause(self):
        analysis = fixture([(0, 8), (12, 30)])
        result = generate_topic_candidates(analysis)
        validate_candidates(result, analysis)
        self.assertEqual([(c['start'], c['end']) for c in result['candidates']], [(12, 30)])
        self.assertIn('topic_short_segment_skipped', result['warnings'])
        report = segment_topics(analysis)
        self.assertEqual(len(report['segments']), 2)
        self.assertFalse(report['segments'][0]['eligible'])

    def test_long_topic_not_truncated_to_duration_limit(self):
        _, result, report = self.case(['ͬһ����������û�����Ա仯'] * 13)
        self.assertEqual(result['candidate_count'], 0)
        self.assertIn('topic_long_segment_skipped', result['warnings'])
        self.assertEqual([(s['start'], s['end']) for s in report['segments']], [(0, 104)])

    def test_one_long_sentence_is_reported_without_partial_candidate(self):
        analysis = fixture([(0, 120)])
        result = generate_topic_candidates(analysis)
        validate_candidates(result, analysis)
        self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(segment_topics(analysis)['segments'][0]['end'], 120)

    def test_overlapping_sentences_cannot_split(self):
        analysis = fixture([(0, 20), (10, 30), (30, 40), (40, 50)])
        result = generate_topic_candidates(analysis)
        validate_candidates(result, analysis)
        for candidate in result['candidates']:
            self.assertEqual('s0' in candidate['source_sentences'], 's1' in candidate['source_sentences'])
        self.assertEqual(segment_topics(analysis)['units'][0]['source_sentences'], ['s0', 's1'])

    def test_touching_sentences_are_not_hard_pause_with_zero_gap_limit(self):
        _, result, _ = self.case(['��ͬ������ͬ����'] * 3,
                                 options=TopicOptions(max_gap_seconds=0))
        self.assertEqual(result['candidate_count'], 1)
        self.assertEqual(result['candidates'][0]['duration_seconds'], 24)

    def test_empty_analysis_and_uninformative_text_do_not_invent_valleys(self):
        _, result, report = self.case([])
        self.assertEqual(result['candidate_count'], 0)
        self.assertEqual(report['segments'], [])
        self.assertIn('no_sentences', result['warnings'])
        _, result, report = self.case(['������'] * 5)
        self.assertIn('topic_uninformative_text', result['warnings'])
        self.assertTrue(all(b['similarity'] is None for b in report['boundaries']))
        self.assertEqual(len(report['segments']), 1)

    def test_source_and_options_are_bound_to_deterministic_ids(self):
        analysis, result, report = self.case(['�����������'] * 4 + ['̫����Զ������'] * 4)
        original = deepcopy(analysis)
        self.assertEqual(result, generate_topic_candidates(analysis))
        self.assertEqual(report, segment_topics(analysis))
        changed = generate_topic_candidates(analysis, replace(TopicOptions(), min_depth=.16))
        self.assertTrue(set(c['id'] for c in result['candidates']).isdisjoint(
            c['id'] for c in changed['candidates']))
        self.assertEqual(analysis, original)

    def test_partial_topic_or_missing_candidate_or_changed_id_is_rejected(self):
        analysis, baseline, _ = self.case(['�����������'] * 4 + ['̫����Զ������'] * 4)
        for change in ('partial', 'missing', 'id', 'warnings', 'options'):
            corrupted = deepcopy(baseline)
            if change == 'partial':
                item = corrupted['candidates'][0]
                item.update(end=24, duration_seconds=24, source_sentences=['s0', 's1', 's2'],
                            text='\n'.join(s['text'] for s in analysis['sentences'][:3]))
            elif change == 'missing':
                corrupted['candidates'].pop()
                corrupted['candidate_count'] -= 1
            elif change == 'id':
                corrupted['candidates'][0]['id'] = 'c_' + '0' * 64
            elif change == 'warnings':
                corrupted['warnings'].append('topic_short_segment_skipped')
            else:
                corrupted['generator']['options']['window_units'] = 2
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_candidates(corrupted, analysis)

    def test_rule_scoring_preserves_topic_identity_and_boundaries(self):
        analysis, result, _ = self.case(['�����������'] * 4 + ['̫����Զ������'] * 4)
        scored = score_candidates(result, analysis)
        validate_scored(scored, analysis)
        self.assertEqual(scored['generator'], result['generator'])
        self.assertEqual({(c['id'], c['start'], c['end']) for c in scored['candidates']},
                         {(c['id'], c['start'], c['end']) for c in result['candidates']})

    def test_generation_defaults_and_explicit_legacy_baseline(self):
        analysis = text_analysis(['�����������'] * 4)
        with patch.dict(os.environ, {}, clear=True):
            snapshot = settings_snapshot()
            self.assertEqual(snapshot['version'], LEGACY)
            self.assertEqual(generate_configured_candidates(analysis), generate_candidates(analysis))
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': TOPIC}):
            self.assertEqual(generate_configured_candidates(analysis), generate_topic_candidates(analysis))
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': LEGACY}):
            self.assertEqual(generate_configured_candidates(analysis), generate_candidates(analysis))
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': 'unknown'}), self.assertRaises(ValueError):
            settings_snapshot()

    def test_generation_snapshot_does_not_read_later_environment_changes(self):
        analysis = text_analysis(['�����������'] * 4)
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': TOPIC}):
            settings = settings_snapshot()
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': LEGACY}):
            self.assertEqual(generate_configured_candidates(analysis, settings=settings),
                             generate_topic_candidates(analysis))

    def test_topic_mode_disables_sentence_repair_and_legacy_keeps_it(self):
        boundary = {'mode': 'sentence', 'config': {'example': 1}}
        topic = {'version': TOPIC, 'options': asdict(TopicOptions())}
        self.assertEqual(effective_boundary_settings(boundary, topic), {'mode': 'off'})
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': LEGACY}):
            self.assertEqual(effective_boundary_settings(boundary, settings_snapshot()), boundary)
        self.assertEqual(boundary['mode'], 'sentence')

    def test_invalid_option_values_and_unknown_snapshot_keys_are_rejected(self):
        for kwargs in ({'window_units': True}, {'window_units': 0}, {'window_units': 33},
                       {'min_segment_units': 0}, {'min_depth': float('nan')}, {'min_depth': 2}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TopicOptions(**kwargs)
        with self.assertRaises(ValueError):
            validate_settings({'version': TOPIC, 'options': asdict(TopicOptions()), 'extra': 1})
        with self.assertRaises(ValueError):
            validate_settings({'version': TOPIC, 'options': {}})


if __name__ == '__main__':
    unittest.main()
