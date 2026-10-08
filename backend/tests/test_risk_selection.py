from copy import deepcopy
import unittest

from backend.tests.test_candidates import fixture
from backend.tests.test_continuity import sample
from clipforge.decision.candidates import generate_candidates, content_hash
from clipforge.decision.scoring import score_candidates
from clipforge.decision.selection import build_selection, temporal_iou
from clipforge.decision.risk_selection import build_risk_selection


class RiskSelectionTests(unittest.TestCase):
    def test_risky_candidates_cannot_displace_overlapping_unflagged_survivor(self):
        analysis, scored = sample()
        before = content_hash([analysis, scored])
        baseline = build_selection(scored, analysis)
        report = build_risk_selection(scored, analysis)
        by_id = {c['id']: c for c in scored['candidates']}
        notes = {i['candidate_id']: i for i in report['items']}
        flagged = [i['selection_order'] for i in report['items'] if i['text_review']]
        unflagged = [i['selection_order'] for i in report['items'] if not i['text_review']]
        self.assertTrue(flagged and unflagged)
        self.assertLess(max(unflagged), min(flagged))
        for i in report['items']:
            if i['suppressed_by']:
                suppressor = notes[i['suppressed_by']]
                self.assertTrue(suppressor['retained'])
                self.assertLess(suppressor['selection_order'], i['selection_order'])
                self.assertGreaterEqual(temporal_iou(by_id[i['candidate_id']], by_id[i['suppressed_by']]), .5)
                if not i['text_review']:
                    self.assertIsNone(suppressor['text_review'])
        kept = [by_id[i['candidate_id']] for i in report['items'] if i['retained']]
        self.assertTrue(all(temporal_iou(a,b) < .5 for j,a in enumerate(kept) for b in kept[j+1:]))
        topic_ids = {cid for g in report['topics']['groups'] for cid in g['member_ids']}
        self.assertEqual(topic_ids, {c['id'] for c in kept})
        self.assertEqual(before, content_hash([analysis, scored]))
        self.assertEqual(baseline, build_selection(scored, analysis))
        self.assertEqual(report, build_risk_selection(scored, analysis))
        self.assertFalse(report['automatic_acceptance'])

    def test_all_flagged_still_keep_candidates_for_review(self):
        analysis = fixture([(0,20), (30,50)])
        for sentence, word in zip(analysis['sentences'], analysis['words']):
            sentence['text'] = word['text'] = '这台设备已经'
        scored = score_candidates(generate_candidates(analysis), analysis)
        report = build_risk_selection(scored, analysis)
        self.assertEqual(report['summary']['retained_count'], 2)
        self.assertEqual(report['summary']['retained_text_warning_count'], 2)
        self.assertTrue(all(i['boundary_status'] == 'review_required' for i in report['items']))

    def test_no_warning_preserves_original_retained_set_and_does_not_mark_complete(self):
        analysis = fixture([(0,20), (21,40), (41,60)])
        scored = score_candidates(generate_candidates(analysis), analysis)
        report = build_risk_selection(scored, analysis)
        self.assertTrue(all(i['retained'] == i['baseline_retained'] for i in report['items']))
        self.assertTrue(all(i['boundary_status'] == 'not_verified' for i in report['items']))

    def test_empty_and_invalid_inputs_use_existing_validation(self):
        analysis = fixture([])
        scored = score_candidates(generate_candidates(analysis), analysis)
        self.assertEqual(build_risk_selection(scored, analysis)['items'], [])
        for threshold in (0, True, float('nan'), 1.1):
            with self.assertRaises(ValueError):
                build_risk_selection(scored, analysis, iou_threshold=threshold)
        analysis, scored = sample()
        corrupted = deepcopy(scored)
        corrupted['candidates'][0]['score'] += 1
        with self.assertRaises(ValueError):
            build_risk_selection(corrupted, analysis)


if __name__ == '__main__':
    unittest.main()
