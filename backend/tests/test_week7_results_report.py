"""Fabricated summaries only; never reads real human feedback or campaign data."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / 'tools' / 'week7_review' / 'report_human_results.py'
SPEC = importlib.util.spec_from_file_location('results_report', SCRIPT)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)
frozen = reporter.frozen


def refresh(summary):
    rows = summary['paired_answers']
    summary['statistics'] = frozen.aggregate(rows)
    for label, value in summary['strata'].items():
        value['statistics'] = frozen.aggregate([row for row in rows if row['exposure_stratum'] == label])
    return summary


def fabricated(counts=None):
    counts = counts or [4] * 9 + [5] * 21
    rows = []
    for source, count in enumerate(counts):
        for slot in range(count):
            rows.append({'sample_id': f'R{source * 5 + slot + 1:04d}', 'sha256': '2' * 64,
                'batch_index': source // 5 + 1, 'video_id': f'V{source + 1:03d}',
                'exposure_stratum': list(reporter.STRATA)[source >= 21],
                **{rater: dict.fromkeys(frozen.FIELDS, 'yes') for rater in ('A', 'B')}})
    n = len(rows)
    groups = []
    for index in range(1, 7):
        group_n = sum(row['batch_index'] == index for row in rows)
        groups.append({'batch_index': index, 'batch_id': '3' * 64, 'manifest_sha256': '4' * 64,
            'sample_count': group_n, 'status': 'complete' if group_n else 'not_applicable',
            'raters': {rater: {'status': 'complete', 'human_review_complete': True,
                'file': 'C:/PRIVATE_PERSON/SECRET-original-video.json'} if group_n else
                {'status': 'not_applicable', 'human_review_complete': False, 'na_export_files': []}
                for rater in ('A', 'B')}})
    completed = sum(count > 0 for count in counts)
    sc = {'completed': completed}
    if completed < 30:
        sc['completed_no_candidates'] = 30 - completed
    nonempty = sum(group['sample_count'] > 0 for group in groups)
    strata = {}
    for label, start, end in zip(reporter.STRATA, (0, 21), (21, 30)):
        rateable = sum(count > 0 for count in counts[start:end])
        source_counts = {'completed': rateable} if rateable else {}
        if rateable < end - start:
            source_counts['completed_no_candidates'] = end - start - rateable
        strata[label] = {'fixed_source_denominator': end - start, 'planned_clip_slots': (end - start) * 5,
            'actual_published_clip_count': sum(counts[start:end]), 'source_status_counts': source_counts,
            'statistics': None, 'interpretation': 'C:/PRIVATE_PERSON/SECRET-interpretation'}
    summary = {'schema_version': 'week7-formal30-human-summary-v1', 'campaign_id': '1' * 64,
        'generated_at': '2026-11-02T00:00:00Z', 'scope': 'formal30_mixed_exposure', 'status': 'complete',
        'all30_ready': True, 'all_six_manifests_ready': True, 'complete': True, 'coverage_complete': True,
        'fixed_source_denominator': 30, 'planned_clip_slots': 150, 'actual_rateable_clip_count': n,
        'actual_count_final': True, 'unfilled_planned_slots': 150 - n,
        'source_outcomes': {'status_counts': sc, 'failed_sources': [],
            'no_candidate_sources': [f'V{source + 1:03d}' for source, count in enumerate(counts) if not count],
            'pending_sources': [], 'planned_slots_lost_to_source_failure': 0,
            'planned_slots_unfilled_no_candidates': (30 - completed) * 5,
            'planned_slots_unfilled_insufficient_candidates': completed * 5 - n,
            'source_failure_is_a_human_rejection': False},
        'human_review_coverage': {'expected_nonempty_groups_per_rater': nonempty,
            'completed_nonempty_groups_per_rater': {'A': nonempty, 'B': nonempty},
            'completed_paired_nonempty_groups': nonempty, 'pooled_paired_rows_available': n,
            'empty_groups_not_counted_as_completed_human_reviews': 6 - nonempty,
            'empty_na_exports_missing_nonblocking': [], 'pending_reviews': [], 'missing_frozen_manifest_groups': []},
        'groups': groups, 'statistics': None, 'strata': strata, 'paired_answers': rows,
        'input_audit': {key: [] for key in reporter.AUDIT_KEYS}, 'required_action': None,
        'limits': ['C:/PRIVATE_PERSON/SECRET-limit']}
    summary['input_audit']['individual_complete_semantic_records'] = 2 * nonempty
    return refresh(summary)


class ResultsReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-week7-public-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'SECRET-private-summary.json'

    def build(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')
        return reporter.build_report(self.path)

    def test_complete_report_preserves_primary_and_stratum_statistics(self):
        summary = fabricated()
        value = self.build(summary)
        self.assertEqual(value['statistics'], summary['statistics'])
        self.assertEqual(value['actual_rateable_clip_count'], 141)
        self.assertEqual(value['unfilled_planned_slots'], 9)
        self.assertEqual(value['provenance']['upstream_summary_sha256'], frozen.file_hash(self.path))
        for label in reporter.STRATA:
            self.assertEqual(value['strata'][label]['statistics'], summary['strata'][label]['statistics'])

    def test_pending_or_incomplete_summaries_are_rejected(self):
        for key, bad in (('status', 'pending'), ('complete', False), ('coverage_complete', False),
                         ('all30_ready', False), ('statistics', None), ('paired_answers', None)):
            with self.subTest(key=key):
                value = fabricated()
                value[key] = bad
                with self.assertRaises(ValueError):
                    self.build(value)

    def test_missing_or_invalid_answers_are_rejected(self):
        for bad in (None, 'unknown', True):
            value = fabricated()
            value['paired_answers'][0]['A']['accept'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.build(value)
        value = fabricated()
        del value['paired_answers'][0]['B']['accept']
        with self.assertRaises(ValueError):
            self.build(value)

    def test_denominator_mismatches_are_rejected(self):
        for key, bad in (('fixed_source_denominator', 29), ('planned_clip_slots', 149),
                         ('actual_rateable_clip_count', 140), ('unfilled_planned_slots', 8)):
            value = fabricated()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.build(value)
        value = fabricated()
        value['groups'][0]['sample_count'] -= 1
        with self.assertRaises(ValueError):
            self.build(value)

    def test_duplicates_and_cross_source_slot_aliasing_are_rejected(self):
        value = fabricated()
        value['paired_answers'][1] = deepcopy(value['paired_answers'][0])
        with self.assertRaises(ValueError):
            self.build(value)
        value = fabricated()
        value['paired_answers'][1]['video_id'] = 'DIFFERENT'
        with self.assertRaises(ValueError):
            self.build(value)

    def test_statistics_or_stratum_tampering_is_rejected(self):
        for mutate in (
                lambda value: value['statistics']['per_rater']['A']['accept'].update(yes_count=140),
                lambda value: value['strata']['development_known'].update(actual_published_clip_count=1),
                lambda value: value['strata']['development_known'].update(fixed_source_denominator=20),
                lambda value: value['paired_answers'][0].update(exposure_stratum='unknown'),
                lambda value: value['paired_answers'][0].update(exposure_stratum='newly_added_user_attested_distinct')):
            value = fabricated()
            mutate(value)
            with self.assertRaises(ValueError):
                self.build(value)

    def test_unresolved_input_or_missing_group_is_rejected(self):
        for key in ('invalid_files', 'conflicts', 'answers_waiting_for_manifest'):
            value = fabricated()
            value['input_audit'][key] = [{'file': 'SECRET'}]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.build(value)
        value = fabricated()
        value['groups'].pop()
        with self.assertRaises(ValueError):
            self.build(value)

    def test_uncertain_remains_a_category_and_accept_overlap_is_not_consensus(self):
        value = fabricated()
        first = value['paired_answers'][:3]
        first[0]['A']['accept'] = 'uncertain'
        first[0]['B']['accept'] = 'no'
        first[1]['A']['accept'] = 'yes'
        first[1]['B']['accept'] = 'uncertain'
        first[2]['A']['opening_complete'] = 'uncertain'
        result = self.build(refresh(value))
        margins = result['supplementary']['question_margins']
        self.assertEqual(margins['A']['accept'], {'yes': 140, 'no': 0, 'uncertain': 1})
        self.assertEqual(result['statistics']['accept_nominal_three_class']['denominator'], 141)
        self.assertEqual(result['statistics']['per_rater']['A']['boundary']['yes_count'], 140)
        overlap = result['supplementary']['accept_overlap']
        self.assertEqual([overlap[key] for key in ('both_yes', 'one_yes', 'neither_yes')], [139, 1, 1])
        self.assertEqual(result['supplementary']['questions_by_non_yes_count']['A'][0]['question'], 'opening_complete')

    def test_source_balanced_means_do_not_replace_clip_weighted_rates(self):
        value = fabricated([1] + [2] * 29)
        value['paired_answers'][0]['A'] = dict.fromkeys(frozen.FIELDS, 'no')
        result = self.build(refresh(value))
        self.assertAlmostEqual(result['statistics']['per_rater']['A']['accept']['yes_rate'], 58 / 59)
        balanced = result['supplementary']['source_balanced']
        self.assertEqual(balanced['rateable_source_count'], 30)
        self.assertAlmostEqual(balanced['per_rater']['A']['accept'], 29 / 30)

    def test_empty_sources_are_supported_without_invented_human_rejections(self):
        result = self.build(fabricated([0] * 5 + [1] * 25))
        self.assertEqual(result['completed_paired_group_count'], 5)
        balanced = result['supplementary']['source_balanced']
        self.assertEqual(balanced['rateable_source_count'], 25)
        self.assertEqual(balanced['sources_without_rateable_clips'], 5)
        self.assertEqual(balanced['per_rater']['A']['accept'], 1)
        self.assertEqual(result['unfilled_planned_slots'], 125)

    def test_public_report_contains_no_identifiers_raw_answers_or_paths(self):
        result = self.build(fabricated())
        text = json.dumps(result)
        for forbidden in ('SECRET', 'PRIVATE_PERSON', 'C:/', 'paired_answers', 'sample_id', 'video_id', 'R0001', 'V001'):
            self.assertNotIn(forbidden, text)
        self.assertFalse(result['privacy']['individual_answers_published'])

    def test_cli_uses_exclusive_output_and_does_not_change_input(self):
        self.path.write_text(json.dumps(fabricated()), encoding='utf-8')
        before = self.path.read_bytes()
        output = self.root / 'public.json'
        command = [sys.executable, str(SCRIPT), '--summary', str(self.path), '--output', str(output)]
        first = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(first.returncode, 0, first.stderr)
        public_before = output.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(output.read_bytes(), public_before)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_rejects_pending_without_creating_report(self):
        value = fabricated()
        value['status'] = 'pending'
        self.path.write_text(json.dumps(value), encoding='utf-8')
        output = self.root / 'public.json'
        result = subprocess.run([sys.executable, str(SCRIPT), '--summary', str(self.path), '--output', str(output)],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
