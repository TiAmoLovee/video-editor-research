from copy import deepcopy
import unittest

from clipforge.decision.short_boundary_choices import (
    build_choices, compact_choices, resolve_choices, resolve_compact_choices,
)


def analysis():
    return {'words': [dict(id=f'w{i}', text='解释。', start=i*2, end=i*2+1.8)
                      for i in range(65)]}


class ShortBoundaryChoicesTests(unittest.TestCase):
    def test_all_offered_ranges_obey_constraints_and_are_reproducible(self):
        a = analysis()
        candidates = [dict(id='short', start=20, end=49.8), dict(id='long', start=20, end=89.8)]
        before = deepcopy((a, candidates))
        plan = build_choices(a, candidates)
        self.assertEqual(plan, build_choices(a, candidates))
        self.assertEqual((a, candidates), before)
        for c in plan['candidates']:
            self.assertTrue(c['options'])
            self.assertLessEqual(len(c['options']), 16)
            frames = set()
            for o in c['options']:
                self.assertLessEqual(abs(o['start']-c['start']), 6)
                self.assertLessEqual(abs(o['end']-c['end']), 6)
                self.assertLessEqual(o['end']-o['start'], min(c['end']-c['start']+6, max(45, c['end']-c['start']))+1e-8)
                self.assertTrue(15 <= o['duration_seconds'] <= 90)
                frames.add((o['start_frame'], o['end_frame_exclusive']))
            self.assertEqual(len(frames), len(c['options']))

    def test_every_resolution_is_unscored_and_needs_human_judgment(self):
        plan = build_choices(analysis(), [dict(id='A', start=20, end=49.8)])
        for choice in ['keep', 'uncertain'] + [o['id'] for o in plan['candidates'][0]['options']]:
            row = resolve_choices(plan, {'items': [['A', choice]]})[0]
            self.assertIsNone(row['score'])
            self.assertIsNone(row['human_pass'])

    def test_model_cannot_supply_times_or_other_candidates_options(self):
        plan = build_choices(analysis(), [dict(id='A', start=20, end=49.8)])
        for response in ({'items': []}, {'items': [['B', 'keep']]}, {'items': [['A', 'O99']]},
                         {'items': [['A', 'keep', 0, 120]]}, {'items': [['A', True]]},
                         {'items': [['A', 'keep']], 'start': 0}):
            with self.assertRaises(ValueError):
                resolve_choices(plan, response)

    def test_long_gap_and_overlapping_excluded_words_are_not_offered(self):
        a = analysis()
        a['words'] = [w for w in a['words'] if not 30 <= w['start'] < 40]
        p = build_choices(a, [dict(id='A', start=20, end=49.8)])
        self.assertEqual(p['candidates'][0]['options'], [])
        a = analysis()
        a['words'][0]['end'] = 120
        p = build_choices(a, [dict(id='A', start=20, end=49.8)])
        self.assertEqual(p['candidates'][0]['options'], [])

    def test_unchanged_frame_range_is_not_presented_as_adjustment(self):
        p = build_choices(analysis(), [dict(id='A', start=20.001, end=49.799)])
        self.assertNotIn((600, 1494), [(o['start_frame'], o['end_frame_exclusive'])
                                      for o in p['candidates'][0]['options']])

    def test_invalid_times_and_duplicate_ids_fail(self):
        for cs in ([dict(id='A', start=True, end=30)], [dict(id='A', start=0, end=float('nan'))],
                   [dict(id='A', start=0, end=30)]*2):
            with self.assertRaises(ValueError):
                build_choices(analysis(), cs)

    def test_exhaustive_retains_farther_legal_boundary_without_rank_truncation(self):
        a = analysis()
        candidate = [dict(id='A', start=20, end=49.8)]
        v1 = build_choices(a, candidate)
        v2 = build_choices(a, candidate, exhaustive=True)
        ranges = lambda p: {(o['start_frame'], o['end_frame_exclusive'])
                            for o in p['candidates'][0]['options']}
        self.assertLess(ranges(v1), ranges(v2))
        self.assertIn((600, 1674), ranges(v2))  # End +6 seconds; distant but legal.
        self.assertEqual(v2['version'], 'short-boundary-choices-v2')
        self.assertFalse(v2['semantic_completeness_verified'])
        self.assertEqual(v2, build_choices(a, candidate, exhaustive=True))

    def test_compact_form_roundtrips_every_pair_without_filling_holes(self):
        plan = build_choices(analysis(), [dict(id='A', start=20, end=49.8)], exhaustive=True)
        # Include disjoint runs, like gaps caused by excluded overlapping words.
        plan['candidates'][0]['options'] = [o for o in plan['candidates'][0]['options'] if o['last_unit'] % 3 != 0]
        compact = compact_choices(plan)
        pairs = {(first, last) for first, runs in compact['candidates'][0]['allowed_pairs']
                 for low, high in runs for last in range(low, high+1)}
        expected = {(o['first_unit'], o['last_unit']) for o in plan['candidates'][0]['options']}
        self.assertEqual(pairs, expected)
        for first, last in pairs:
            row = resolve_compact_choices(plan, {'items': [['A', 'adjust', first, last]]})[0]
            self.assertIsNone(row['human_pass'])
            self.assertIsNone(row['score'])

    def test_individually_legal_edges_cannot_be_recombined_into_illegal_pair(self):
        plan = build_choices(analysis(), [dict(id='A', start=20, end=49.8)], exhaustive=True)
        options = plan['candidates'][0]['options']
        first = min(o['first_unit'] for o in options)
        last = max(o['last_unit'] for o in options)
        self.assertFalse(any(o['first_unit'] == first and o['last_unit'] == last for o in options))
        with self.assertRaises(ValueError):
            resolve_compact_choices(plan, {'items': [['A', 'adjust', first, last]]})

    def test_exhaustive_limit_fails_instead_of_silently_truncating(self):
        candidate = [dict(id='A', start=20, end=49.8)]
        with self.assertRaisesRegex(ValueError, 'no partial shortlist'):
            build_choices(analysis(), candidate, exhaustive=True, max_options_per_candidate=1)
        for kwargs in ({'exhaustive': 1}, {'max_options_per_candidate': True},
                       {'max_options_per_candidate': 0}, {'max_options_per_candidate': 10001}):
            with self.assertRaises(ValueError):
                build_choices(analysis(), candidate, **kwargs)

    def test_compact_response_is_strict_and_abstention_is_not_a_pass(self):
        plan = build_choices(analysis(), [dict(id='A', start=20, end=49.8)], exhaustive=True)
        for status in ('keep', 'uncertain'):
            row = resolve_compact_choices(plan, {'items': [['A', status, None, None]]})[0]
            self.assertIsNone(row['human_pass'])
        for row in (['A', 'adjust', True, 25], ['A', 'keep', 10, 25],
                    ['B', 'uncertain', None, None], ['A', 'adjust', 10.0, 25],
                    ['A', 'adjust', -1, 25], ['A', 'adjust', 10, 999]):
            with self.assertRaises(ValueError):
                resolve_compact_choices(plan, {'items': [row]})


if __name__ == '__main__':
    unittest.main()
