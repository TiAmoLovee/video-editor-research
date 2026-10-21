"""Whole discourse dependencies, coverage tradeoffs and versioned generation."""

from copy import deepcopy
from dataclasses import asdict
import os
import unittest
from unittest.mock import patch

from test_candidates import fixture
from clipforge.decision.generation import (
    EVENT_TOPIC, effective_boundary_settings, generate_configured_candidates, settings_snapshot,
)
from clipforge.decision.topic_segments import (
    EVENT_GENERATOR_VERSION as V3, TopicOptions, generate_topic_candidates, segment_topics,
)
from clipforge.decision.validation import validate_candidates


def two_topics(left_tail=None, right_head=None, seconds=8):
    data = fixture([(i * (seconds + .5), i * (seconds + .5) + seconds) for i in range(8)])
    for index, (word, sentence) in enumerate(zip(data['words'], data['sentences'])):
        text = ('cooking kitchen recipe ' if index < 4 else 'astronomy galaxy telescope ') * 4
        if index == 3 and left_tail is not None:
            text += left_tail
        elif index == 4 and right_head is not None:
            text = right_head + text
        else:
            text += '。'
        word['text'] = sentence['text'] = text
    return data


class EventTopicGenerationTests(unittest.TestCase):
    def check_linked_cut(self, data):
        original = deepcopy(data)
        old = generate_topic_candidates(data, version='topic-valley-v2')
        self.assertEqual(old['candidate_count'], 2)
        new = generate_topic_candidates(data, version=V3)
        validate_candidates(new, data)
        self.assertEqual(new['candidate_count'], 1)
        self.assertEqual(new['candidates'][0]['source_sentences'],
                         [sentence['id'] for sentence in data['sentences']])
        report = segment_topics(data, version=V3)
        cut = report['boundaries'][3]
        self.assertEqual(cut['reasons'], ['event_dependency_crosses_cut'])
        self.assertGreater(cut['event_support']['active_dependencies'], 0)
        self.assertFalse(report['event_policy']['semantic_completeness_verified'])
        self.assertEqual(data, original)
        return new, report

    def test_question_and_answer_survive_vocabulary_change_and_pause(self):
        self.check_linked_cut(two_topics('为什么改变计划？', '预算变了。'))

    def test_modifier_and_head_are_kept_before_generating_candidate(self):
        self.check_linked_cut(two_topics('我们讨论那个', '阶段的变化。'))

    def test_introduction_and_quote_spans_are_indivisible(self):
        for data in (two_topics('请听说明：', '这是说明。'),
                     two_topics('他说：“这个方法。', '换了一种做法。”')):
            with self.subTest(data=data['sentences'][3]['text']):
                self.check_linked_cut(data)

    def test_independent_topics_remain_short_instead_of_merging_everything(self):
        data = two_topics()
        old = generate_topic_candidates(data, version='topic-valley-v2')
        new = generate_topic_candidates(data, version=V3)
        self.assertEqual([(c['start'], c['end']) for c in new['candidates']],
                         [(c['start'], c['end']) for c in old['candidates']])
        self.assertEqual(segment_topics(data, version=V3)['event_dependencies']['links'], [])

    def test_rejected_event_cut_does_not_release_a_conflicting_nearby_cut(self):
        texts = ['alpha.', 'alpha.', 'alpha?', 'beta.', 'beta.', 'gamma.', 'gamma.', 'gamma.']
        data = fixture([(i * 8, (i + 1) * 8) for i in range(8)])
        for word, sentence, text in zip(data['words'], data['sentences'], texts):
            word['text'] = sentence['text'] = text
        options = TopicOptions(window_units=1, min_segment_units=3)
        old = segment_topics(data, options, version='topic-valley-v2')
        new = segment_topics(data, options, version=V3)
        self.assertEqual(old['boundaries'][2]['decision'], 'accepted_valley')
        self.assertEqual(new['boundaries'][2]['reasons'], ['event_dependency_crosses_cut'])
        self.assertEqual(new['boundaries'][4]['reasons'], ['min_segment_units_conflict'])
        self.assertEqual([(s['first_unit'], s['last_unit']) for s in new['segments']], [(0, 7)])

    def test_whole_linked_event_over_limit_is_visible_without_short_partial_fallback(self):
        data = two_topics('为什么改变计划？', '预算变了。', seconds=12)
        self.assertEqual(generate_topic_candidates(data, version='topic-valley-v2')['candidate_count'], 2)
        new, report = generate_topic_candidates(data, version=V3), segment_topics(data, version=V3)
        self.assertEqual(new['candidate_count'], 0)
        self.assertIn('topic_long_segment_skipped', new['warnings'])
        self.assertEqual(report['segments'][0]['source_sentences'], [f's{i}' for i in range(8)])
        self.assertEqual(report['segments'][0]['reasons'], ['above_max_seconds'])

    def test_dependency_across_hard_pause_excludes_fragments_without_bridging(self):
        data = fixture([(0, 20), (25, 45)])
        for word, sentence, text in zip(data['words'], data['sentences'], ['为什么改变？', '预算减少了。']):
            word['text'] = sentence['text'] = text
        data['speech'] = [{'start': 0, 'end': 20}, {'start': 25, 'end': 45}]
        data['silence'] = [{'start': 20, 'end': 25}, {'start': 45, 'end': 46}]
        self.assertEqual(generate_topic_candidates(data, version='topic-valley-v2')['candidate_count'], 2)
        new, report = generate_topic_candidates(data, version=V3), segment_topics(data, version=V3)
        self.assertEqual(new['candidate_count'], 0)
        self.assertEqual(len(report['segments']), 2)
        self.assertTrue(all('unresolved_event_dependency' in s['reasons'] for s in report['segments']))
        self.assertEqual(report['event_dependencies']['links'][0]['retention'], 'interrupted_by_hard_pause')
        self.assertEqual(report['boundaries'][0]['decision'], 'hard_pause')
        validate_candidates(new, data)

    def test_source_end_missing_answer_excluded_but_ambiguous_demonstrative_only_warns(self):
        for text, count, warning in (('为什么改变计划？', 0, 'topic_event_dependency_unresolved'),
                                     ('我就选这个', 1, 'topic_event_dependency_warning')):
            with self.subTest(text=text):
                data = fixture([(0, 20)])
                data['words'][0]['text'] = data['sentences'][0]['text'] = text
                result = generate_topic_candidates(data, version=V3)
                self.assertEqual(result['candidate_count'], count)
                self.assertIn(warning, result['warnings'])
                validate_candidates(result, data)

    def test_quote_missing_closer_does_not_offer_later_quotation_fragments(self):
        data = two_topics('他说：“这个方法。', '新的方法。')
        report = segment_topics(data, version=V3)
        self.assertTrue(all('unresolved_event_dependency' in s['reasons'] for s in report['segments']))
        self.assertEqual(generate_topic_candidates(data, version=V3)['candidate_count'], 0)

    def test_snapshot_new_identity_empty_input_and_legacy_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings_snapshot()['version'], 'sentence-window-v1')
        with patch.dict(os.environ, {'CLIPFORGE_CANDIDATE_GENERATOR': EVENT_TOPIC}):
            settings = settings_snapshot()
            generated = generate_configured_candidates(two_topics())
        self.assertEqual(generated['generator'], settings)
        self.assertEqual(effective_boundary_settings({'mode': 'sentence'}, settings), {'mode': 'off'})
        self.assertEqual(settings['version'], V3)
        old = generate_topic_candidates(two_topics(), version='topic-valley-v2')
        self.assertTrue(set(c['id'] for c in old['candidates']).isdisjoint(
            c['id'] for c in generated['candidates']))
        empty = generate_topic_candidates(fixture([]), version=V3)
        validate_candidates(empty, fixture([]))
        self.assertEqual(empty['candidate_count'], 0)
        self.assertEqual(asdict(TopicOptions()), settings['options'])


if __name__ == '__main__':
    unittest.main()
