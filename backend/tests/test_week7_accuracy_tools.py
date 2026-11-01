"""Synthetic regression checks; no actual human answers or media are used."""
import contextlib
import copy
import hashlib
import importlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO / 'tools/week7_validation/accuracy'
PACKAGE_NAME = 'week7_accuracy_validation_tests'
if PACKAGE_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME, PACKAGE_DIR / '__init__.py', submodule_search_locations=[str(PACKAGE_DIR)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)
score_module = importlib.import_module(PACKAGE_NAME + '.score_human_accuracy')
combine = score_module.combine
cut_match = score_module.cut_match
endpoint_error = score_module.endpoint_error
transcript_metrics = score_module.transcript_metrics
validate_word_reference = score_module.validate_word_reference
word_metrics = score_module.word_metrics



def transcripts():
    return [{'excerpt_id': f'A{i:02}', 'reference_status': 'complete',
             'text': 'abc', 'complete': True} for i in range(1, 4)]


def hypotheses():
    return {f'A{i:02}': {'words': [{'text': 'abc'}]} for i in range(1, 4)}


def words():
    targets = [{'target_id': f'T{i:02}', 'excerpt_id': f'A{1 if i <= 7 else 2 if i <= 14 else 3:02}',
                'original_word_id': f'SYNTHETIC-{i}', 'predicted_text': 'SYNTHETIC',
                'predicted_start_excerpt_seconds': 1.0,
                'predicted_end_excerpt_seconds': 1.2} for i in range(1, 21)]
    items = [{'target_id': t['target_id'], 'excerpt_id': t['excerpt_id'],
              'original_word_id': t['original_word_id'], 'target_text': t['predicted_text'],
              'reference_status': 'locatable', 'human_start_seconds': 1.1,
              'human_end_seconds': 1.3, 'uncertainty_seconds': .05,
              'note': '', 'complete': True} for t in targets]
    result = {'schema_version': 'week7-independent-word-timing-result-v1',
        'campaign_id': 'SYNTHETIC', 'manifest_sha256': '0' * 64,
        'selection_private_sha256': '1' * 64, 'blind_reference_sha256': '2' * 64,
        'reviewer': 'SYNTHETIC', 'independence_confirmed': True,
        'fixed_target_denominator': 20, 'items': items, 'complete': True,
        'exported_at': '2026-11-02T12:00:00+08:00'}
    return targets, result


class CharacterMetricsTests(unittest.TestCase):
    def test_all_three_full_references_publish_primary(self):
        out = transcript_metrics(transcripts(), hypotheses())
        self.assertEqual(out['fixed_excerpt_denominator'], 3)
        self.assertEqual(out['primary_reference_characters'], 9)
        self.assertEqual(out['primary_pooled_cer'], 0)

    def test_partial_reference_blocks_primary_without_silently_dropping_it(self):
        refs = transcripts()
        refs[0].update(reference_status='partially_unintelligible', text='a[听不清]c')
        out = transcript_metrics(refs, hypotheses())
        self.assertIsNone(out['primary_pooled_cer'])
        self.assertEqual(out['full_reference_and_prediction_excerpts'], 2)
        self.assertEqual(out['partial_human_reference_excerpts'], 1)
        self.assertEqual(out['available_full_reference_subset_descriptive']['reference_characters'], 6)

    def test_no_speech_counts_actual_insertions_in_pooled_numerator(self):
        refs = transcripts()
        refs[0].update(reference_status='no_speech', text='')
        out = transcript_metrics(refs, hypotheses())
        self.assertEqual(out['primary_reference_characters'], 6)
        self.assertEqual(out['primary_edit_distance'], 3)
        self.assertEqual(out['primary_pooled_cer'], .5)
        self.assertIsNone(out['per_excerpt'][0]['character_metrics']['cer'])

    def test_all_no_speech_keeps_zero_denominator_cer_undefined(self):
        refs = transcripts()
        for row in refs:
            row.update(reference_status='no_speech', text='')
        out = transcript_metrics(refs, hypotheses())
        self.assertIsNone(out['primary_pooled_cer'])
        self.assertEqual(out['primary_edit_distance'], 9)

    def test_failed_prediction_blocks_primary_and_retains_three_slots(self):
        pred = hypotheses()
        del pred['A02']
        out = transcript_metrics(transcripts(), pred)
        self.assertIsNone(out['primary_pooled_cer'])
        self.assertEqual(out['fixed_excerpt_denominator'], 3)
        self.assertEqual(out['failed_machine_prediction_excerpts'], 1)

    def test_empty_recognition_is_deletions_not_a_missing_prediction(self):
        pred = hypotheses()
        pred['A01']['words'] = []
        out = transcript_metrics(transcripts(), pred)
        self.assertAlmostEqual(out['primary_pooled_cer'], 3 / 9)
        self.assertEqual(out['failed_machine_prediction_excerpts'], 0)

    def test_punctuation_normalization_does_not_rewrite_number_meanings(self):
        refs = transcripts()
        pred = hypotheses()
        refs[0]['text'] = 'Ａ，B C！'
        pred['A01']['words'] = [{'text': 'abc'}]
        self.assertEqual(transcript_metrics(refs, pred)['primary_pooled_cer'], 0)
        refs[0]['text'] = '三'
        pred['A01']['words'] = [{'text': '3'}]
        self.assertGreater(transcript_metrics(refs, pred)['primary_pooled_cer'], 0)


class TimingTests(unittest.TestCase):
    def test_uncertainty_interval_can_block_a_point_pass(self):
        result = endpoint_error(1.29, 1, .05)
        self.assertEqual(result['status'], 'uncertain')
        self.assertAlmostEqual(result['maximum_absolute_error_bound_seconds'], .34)

    def test_entire_error_interval_outside_threshold_is_failure(self):
        self.assertEqual(endpoint_error(1.4, 1, .05)['status'], 'fail')

    def test_threshold_boundary_including_quantization_is_pass(self):
        self.assertEqual(endpoint_error(1.2, 1, .1)['status'], 'pass')

    def test_boolean_and_nan_are_not_numeric_manual_times(self):
        for value in (True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                endpoint_error(1, value, .1)

    def test_mismatch_and_unlocatable_remain_in_twenty_denominator(self):
        targets, result = words()
        for item, status in zip(result['items'][:2], ('mismatch', 'unlocatable')):
            item.update(reference_status=status, human_start_seconds=None,
                        human_end_seconds=None, uncertainty_seconds=None)
        out = word_metrics(result['items'], targets)
        self.assertEqual(out['fixed_target_denominator'], 20)
        self.assertEqual(out['verified_both_endpoints_within_threshold_count'], 18)
        self.assertEqual(out['verified_within_threshold_rate_fixed_twenty'], .9)
        self.assertEqual(out['measurable_target_count'], 18)
        self.assertEqual(out['measurable_endpoint_count'], 36)
        self.assertEqual(out['timing_classification_counts']['not_measurable'], 2)


class WordIdentityTests(unittest.TestCase):
    def validate(self, result, targets):
        return validate_word_reference(result, {'campaign_id': 'SYNTHETIC'},
                                       '0' * 64, '1' * 64, '2' * 64, targets)

    def test_full_synthetic_word_reference_validates(self):
        targets, result = words()
        self.assertIs(self.validate(result, targets), result)

    def test_identity_schema_independence_and_completion_fail_closed(self):
        targets, original = words()
        for changes in ({'campaign_id': 'OTHER'}, {'manifest_sha256': '9' * 64},
                        {'selection_private_sha256': '9' * 64}, {'blind_reference_sha256': '9' * 64},
                        {'fixed_target_denominator': 19}, {'independence_confirmed': False},
                        {'complete': False}, {'schema_version': 'OTHER'}, {'extra': 1}):
            value = copy.deepcopy(original)
            value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(value, targets)

    def test_missing_duplicate_or_changed_targets_fail(self):
        targets, original = words()
        short = copy.deepcopy(original)
        short['items'].pop()
        with self.assertRaises(ValueError):
            self.validate(short, targets)
        for changes in ({'target_id': 'T02'}, {'target_text': 'CHANGED'}, {'original_word_id': 'OTHER'}):
            value = copy.deepcopy(original)
            value['items'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(value, targets)

    def test_mismatch_cannot_carry_invented_times(self):
        targets, result = words()
        result['items'][0]['reference_status'] = 'mismatch'
        with self.assertRaises(ValueError):
            self.validate(result, targets)
        result['items'][0].update(human_start_seconds=None, human_end_seconds=None, uncertainty_seconds=None)
        self.assertIs(self.validate(result, targets), result)

    def test_invalid_numeric_time_ranges_and_infinite_uncertainty_fail(self):
        targets, original = words()
        for changes in ({'human_start_seconds': True}, {'human_start_seconds': -1},
                        {'human_start_seconds': 2}, {'human_end_seconds': 21},
                        {'uncertainty_seconds': None}, {'uncertainty_seconds': float('inf')}):
            value = copy.deepcopy(original)
            value['items'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.validate(value, targets)


class CutMatchingTests(unittest.TestCase):
    def test_nearby_reference_cuts_do_not_share_one_prediction(self):
        out = cut_match([10, 12], [11], 30, 3)
        self.assertEqual((out['tp'], out['fp'], out['fn']), (1, 0, 1))

    def test_negative_control_f1_is_not_automatically_one(self):
        self.assertIsNone(combine([cut_match([], [], 30)])['f1'])
        self.assertEqual(combine([cut_match([], [10], 30)])['f1'], 0)

    def test_reference_boundary_tolerance_does_not_consume_far_prediction(self):
        out = cut_match([10], [6, 13, 17], 30, 3)
        self.assertEqual((out['tp'], out['fp'], out['fn']), (1, 2, 0))




class PortablePathTests(unittest.TestCase):
    def test_import_does_not_open_any_packet_or_real_data(self):
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME + '.import_only_check', PACKAGE_DIR / 'score_human_accuracy.py')
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('data read at import')), \
                mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('data read at import')):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.score))

    def test_explicit_empty_root_never_falls_back_to_an_actual_campaign(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-week7-accuracy-') as directory:
            with self.assertRaisesRegex(ValueError, 'Expected ordinary JSON file'):
                score_module.score(Path(directory))

    def test_cli_requires_explicit_validation_root(self):
        error = io.StringIO()
        with mock.patch.object(sys, 'argv', ['score_human_accuracy.py']), contextlib.redirect_stderr(error):
            with self.assertRaises(SystemExit) as caught:
                score_module.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('--validation-root', error.getvalue())

    def test_pending_packet_can_be_relocated_without_any_media_or_models(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-week7-relocated-') as directory:
            root = Path(directory) / 'arbitrary packet with spaces'
            (root / 'human-reference-pack').mkdir(parents=True)
            (root / 'new-excerpt-asr').mkdir()
            targets, _ = words()
            sources = []
            for i in range(1, 4):
                sources.append({'excerpt_id': f'A{i:02}', 'predeclared_word_targets':
                    [t for t in targets if t['excerpt_id'] == f'A{i:02}']})
            selection = {'new_reference_campaign_id': 'SYNTHETIC', 'sources': sources}
            raw = json.dumps(selection).encode()
            (root / 'new-reference-selection.private.json').write_bytes(raw)
            manifest = {'campaign_id': 'SYNTHETIC', 'parent_campaign_id': 'SYNTHETIC-PARENT',
                'selection_private_sha256': hashlib.sha256(raw).hexdigest(),
                'transcript_excerpts': [{'excerpt_id': f'A{i:02}', 'sha256': str(i) * 64} for i in range(1, 4)]}
            raw = json.dumps(manifest).encode()
            (root / 'human-reference-pack/manifest.public.json').write_bytes(raw)
            summary = {'campaign_id': 'SYNTHETIC', 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                'attempted_top_level_transcribe_calls': 3,
                'runtime': {'device': 'cpu', 'compute_type': 'int8', 'cpu_threads': 4, 'num_workers': 1},
                'model_identity': {'repository': 'SYNTHETIC-MODEL-NOT-LOADED'},
                'result_rows': [{'excerpt_id': f'A{i:02}', 'input_sha256': str(i) * 64,
                                'status': 'failed'} for i in range(1, 4)]}
            (root / 'new-excerpt-asr/summary.private.json').write_text(json.dumps(summary), encoding='utf-8')
            output = score_module.score(root)['public_summary']
            self.assertEqual(output['status'], 'pending_blind_human_reference')
            self.assertEqual(output['new_excerpt_count'], 3)
            self.assertEqual(output['fixed_word_timing_target_count'], 20)
            self.assertEqual(output['machine_exact_excerpt_asr']['completed_excerpts'], 0)
            self.assertIsNone(output['new_excerpt_asr_cer'])
            self.assertIsNone(output['new_whole_source_shot_f1'])
            self.assertIsNone(output['independent_word_timing'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
