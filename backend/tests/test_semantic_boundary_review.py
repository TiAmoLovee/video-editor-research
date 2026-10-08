from copy import deepcopy
import unittest

from clipforge.decision.semantic_boundary_review import build_review_packet, resolve_quote_review
from clipforge.decision.short_boundary_choices import build_choices


def plan():
    return dict(version='short-boundary-choices-v2',
                units=[[1, '开始讨论第一个问题'], [2, '这个问题需要具体回答'], [3, '这里给出完整解释'],
                       [4, '现在解释已经结束'], [5, '接着进入第二个问题']],
                candidates=[dict(id='S01', start=0, end=30, options=[
                    dict(id='O01', first_unit=1, last_unit=4, start=0, end=24, score=None, human_pass=None),
                    dict(id='O02', first_unit=2, last_unit=5, start=6, end=30, score=None, human_pass=None)])])


def response():
    return dict(items=[dict(id='S01', action='adjust', opening='开始讨论第一个问题',
                            ending='现在解释已经结束', reason='保留第一个问题及回答，去掉下一问题。')])


class SemanticBoundaryReviewTests(unittest.TestCase):
    def test_quote_maps_to_local_times_and_does_not_inherit_quality(self):
        p, r = plan(), response()
        before = deepcopy((p, r))
        row = resolve_quote_review(p, r)[0]
        self.assertEqual((row['start'], row['end']), (0, 24))
        self.assertIsNone(row['score'])
        self.assertIsNone(row['human_pass'])
        self.assertFalse(row['automatic_acceptance'])
        self.assertEqual(row['semantic_status'], 'unverified_proposal')
        self.assertEqual((p, r), before)

    def test_only_whitespace_normalization_is_allowed(self):
        r = response()
        r['items'][0]['opening'] = '开始 讨论第一个问题\n'
        self.assertEqual(resolve_quote_review(plan(), r)[0]['option_id'], 'O01')
        for quote in ('开始讨论第一个话题', '开始讨论第一个问题。', '开始讨论……问题'):
            r['items'][0]['opening'] = quote
            with self.assertRaises(ValueError):
                resolve_quote_review(plan(), r)

    def test_quote_starting_inside_unit_cannot_create_a_new_cut(self):
        r = response()
        r['items'][0]['opening'] = '讨论第一个问题'
        with self.assertRaises(ValueError):
            resolve_quote_review(plan(), r)

    def test_each_valid_quote_cannot_be_recombined_into_unlisted_range(self):
        r = response()
        r['items'][0]['ending'] = '接着进入第二个问题'
        with self.assertRaises(ValueError):
            resolve_quote_review(plan(), r)

    def test_repeated_quotes_are_rejected_instead_of_picking_first_occurrence(self):
        p = plan()
        p['units'] += [[6, '开始讨论第一个问题'], [7, '不同的中间内容'], [8, '现在解释已经结束']]
        p['candidates'][0]['options'].append(dict(id='O03', first_unit=6, last_unit=8,
                                                 start=30, end=48, score=None, human_pass=None))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            resolve_quote_review(p, response())

    def test_keep_uncertain_and_structural_errors(self):
        for action in ('keep', 'uncertain'):
            r = response()
            r['items'][0].update(action=action, opening=None, ending=None)
            self.assertIsNone(resolve_quote_review(plan(), r)[0]['human_pass'])
        for change in ({'opening': '问题'}, {'ending': '字'*49}, {'reason': ''}, {'reason': '字'*121},
                       {'action': 'pass'}, {'id': 'S02'}, {'score': 99}, {'action': 'keep'}):
            r = response()
            r['items'][0].update(change)
            with self.assertRaises(ValueError):
                resolve_quote_review(plan(), r)
        for r in ({'items': []}, {'items': response()['items']*2}, {'items': [], 'score': 99}):
            with self.assertRaises(ValueError):
                resolve_quote_review(plan(), r)

    def test_packet_preserves_continuous_original_and_excludes_labels_and_options(self):
        a = dict(words=[dict(id=f'w{i}', text=f'第{i}段原话。', start=i*2, end=i*2+1.8) for i in range(30)])
        p = build_choices(a, [dict(id='S01', start=10, end=39.8)], exhaustive=True)
        packet = build_review_packet(a, p)
        self.assertEqual(packet['content']['transcript'], ''.join(w['text'] for w in a['words']))
        c = packet['content']['candidates'][0]
        self.assertEqual(c['original'], ''.join(w['text'] for w in a['words'][5:20]))
        self.assertEqual(set(c), {'id', 'duration_seconds', 'before', 'original', 'after'})
        self.assertFalse(packet['human_labels_included'])
        a['words'][0]['text'] = '已改动'
        with self.assertRaises(ValueError):
            build_review_packet(a, p)


if __name__ == '__main__':
    unittest.main()
