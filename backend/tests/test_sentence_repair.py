from copy import deepcopy
import unittest

from clipforge.decision.sentence_repair import (
    build_sentence_choices, project_punctuation, resolve_selections, selection_packet,
)


def fixture():
    words = [dict(id=f'w{i}', text=f'这是第{i}次具体讨论已经结束', start=i*2, end=i*2+1.8) for i in range(40)]
    return dict(words=words), ''.join(w['text']+'。' for w in words)


class SentenceRepairTests(unittest.TestCase):
    def test_preserves_source_and_no_labels_are_required(self):
        a, punctuation = fixture()
        c = [dict(id='any-id', start=20, end=49.8)]
        before = deepcopy((a, c))
        plan = build_sentence_choices(a, c, punctuation)
        self.assertEqual(plan, build_sentence_choices(a, c, punctuation))
        self.assertEqual((a, c), before)
        self.assertEqual(plan['projection']['source_text'], ''.join(w['text'] for w in a['words']))
        self.assertFalse(plan['policy']['human_labels_used'])
        self.assertTrue(plan['candidates'][0]['options'])
        packet = selection_packet(plan, 'any-id')
        self.assertEqual(len(packet['options']), len(plan['candidates'][0]['options']))

    def test_small_model_edit_is_recorded_never_written_back(self):
        a, punctuation = fixture()
        punctuation = punctuation.replace('第15次', '第十次')
        result = project_punctuation(a['words'], punctuation)
        self.assertIn('第15次', result['source_text'])
        self.assertNotIn('第十次', result['source_text'])
        self.assertTrue(result['model_text_changes_not_adopted'])

    def test_many_edits_and_semantic_symbol_changes_fail(self):
        a, _ = fixture()
        with self.assertRaises(ValueError):
            project_punctuation(a['words'], '完全不同的内容。')
        with self.assertRaises(ValueError):
            project_punctuation([dict(id='a', text='我用C++完成了这个实验', start=0, end=20)], '我用C完成了这个实验。')

    def test_repeated_ambiguous_context_does_not_create_cuts(self):
        words = [dict(id=f'w{i}', text='这是完全重复的一段原文内容', start=i*2, end=i*2+1.8) for i in range(20)]
        result = project_punctuation(words, ''.join(w['text']+'。' for w in words))
        self.assertEqual(len(result['boundaries']), 1)
        self.assertEqual(result['boundaries'][0]['kind'], 'source_end_not_semantic_verdict')

    def test_all_options_have_bounded_real_ranges_and_no_scores(self):
        a, punctuation = fixture()
        plan = build_sentence_choices(a, [dict(id='A', start=20, end=49.8)], punctuation)
        for o in plan['candidates'][0]['options']:
            self.assertTrue(15 <= o['duration_seconds'] <= 90)
            self.assertLessEqual(abs(o['start']-20), 30)
            self.assertLessEqual(abs(o['end']-49.8), 30)
            self.assertLessEqual(o['end']-o['start'], 59.8+1e-8)
            self.assertIsNone(o['human_pass'])
            self.assertIsNone(o['score'])

    def test_overflow_and_invalid_original_do_not_silently_drop_slots(self):
        a, punctuation = fixture()
        with self.assertRaisesRegex(ValueError, 'no partial cohort'):
            build_sentence_choices(a, [dict(id='A', start=20, end=49.8)], punctuation, max_options=1)
        for c in ([dict(id='A', start=True, end=49.8)], [dict(id='A', start=20.1, end=49.8)],
                  [dict(id='A', start=20, end=49.8)]*2):
            with self.assertRaises(ValueError):
                build_sentence_choices(a, c, punctuation)

    def test_duplicate_selection_and_uncertainty_never_shrink_denominator(self):
        a, punctuation = fixture()
        p = build_sentence_choices(a, [dict(id='A', start=20, end=49.8), dict(id='B', start=22, end=51.8)], punctuation)
        common = next((x, y) for x in p['candidates'][0]['options'] for y in p['candidates'][1]['options']
                      if (x['start_frame'], x['end_frame_exclusive']) == (y['start_frame'], y['end_frame_exclusive']))
        responses = {c['id']: dict(choice=o['id'], reason='test') for c, o in zip(p['candidates'], common)}
        result = resolve_selections(p, responses)
        self.assertEqual(result['denominator'], 2)
        self.assertTrue(all(r['status'] == 'unresolved_duplicate_range' and r['human_pass'] is None for r in result['items']))
        responses = {c['id']: dict(choice='uncertain', reason='test') for c in p['candidates']}
        self.assertEqual(resolve_selections(p, responses)['denominator'], 2)
        with self.assertRaises(ValueError):
            resolve_selections(p, {'A': responses['A']})


if __name__ == '__main__':
    unittest.main()
