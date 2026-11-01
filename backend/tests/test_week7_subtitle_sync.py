"""Tiny fabricated timing fixtures only; never reads real measurements or media."""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / 'tools/week7_validation/subtitle_sync/score_subtitle_sync.py'
SPEC = importlib.util.spec_from_file_location('subtitle_score', PATH)
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


class TimingScoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fabricated-subtitle-times-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        audio = self.root / 'fake-audio.wav'
        audio.write_bytes(b'NOT AUDIO; SYNTHETIC HASH FIXTURE ONLY')
        digest = hashlib.sha256(audio.read_bytes()).hexdigest()
        targets, boundaries, responses = [], [], []
        for i in range(10):
            tid, cue = f'T{i + 1:02d}', f'fake-cue-{i}'
            targets.append({'target_id': tid, 'cue_id': cue, 'audio': 'fake-audio.wav',
                'audio_sha256': digest, 'audio_window_to_output_offset_seconds': 0,
                'audio_duration_seconds': 10})
            boundaries.append({'cue_id': cue, 'start_interval_exact': ['1', '11/10'],
                'end_interval_exact': ['2', '21/10'],
                'file_edge_endpoints': {'start': i == 0, 'end': i == 9}})
            responses.append({'target_id': tid, 'audio_sha256': digest,
                'speech_start': {'status': 'measured', 'earliest_seconds': .99, 'latest_seconds': 1.01},
                'speech_end': {'status': 'measured', 'earliest_seconds': 1.99, 'latest_seconds': 2.01}})
        write(self.root / 'manifest.private.json', {'protocol_id': 'synthetic',
            'render': {'sha256': 'a' * 64}, 'fixed_target_cue_count': 10,
            'expected_speech_endpoint_count': 20, 'targets': targets})
        write(self.root / 'caption-boundaries.private.json', {'mp4_sha256': 'a' * 64, 'targets': boundaries})
        self.observations = {'schema_version': 'week7-subtitle-speech-observations-v1',
            'protocol_id': 'synthetic', 'manifest_sha256': score.sha(self.root / 'manifest.private.json'),
            'observer': 'Fabricated fixture', 'independent_audio_annotation_confirmed': True,
            'exported_at': '2026-11-02T00:00:00Z', 'targets': responses}
        self.path = self.root / 'observations.json'

    def run_score(self, value=None):
        write(self.path, self.observations if value is None else value)
        return score.score(self.root, self.path)

    def test_actual_ranges_pass_only_eighteen_bracketed_endpoints(self):
        result = self.run_score()
        self.assertEqual(result['fixed_endpoint_count'], 20)
        self.assertEqual(result['endpoint_classification_counts'],
            {'within_tolerance': 18, 'file_edge_transition_not_bracketed': 2})
        self.assertEqual(result['cue_classification_counts'], {'within_tolerance': 8, 'not_fully_measurable': 2})

    def test_threshold_uses_exact_fraction_and_wide_ranges_stay_uncertain(self):
        from fractions import Fraction as F
        self.assertEqual(score.classify(F('-0.3'), F('0.3')), 'within_tolerance')
        self.assertEqual(score.classify(F('.31'), F('.4')), 'outside_tolerance')
        self.assertEqual(score.classify(F('.29'), F('.31')), 'uncertain')

    def test_blank_pending_or_unconfirmed_human_input_is_rejected(self):
        for key, value in (('observer', ''), ('independent_audio_annotation_confirmed', False)):
            data = deepcopy(self.observations)
            data[key] = value
            with self.assertRaises(ValueError):
                self.run_score(data)
        data = deepcopy(self.observations)
        data['targets'][0]['speech_start']['earliest_seconds'] = None
        with self.assertRaises(ValueError):
            self.run_score(data)

    def test_missing_or_duplicate_target_is_rejected(self):
        data = deepcopy(self.observations)
        data['targets'].pop()
        with self.assertRaises(ValueError):
            self.run_score(data)
        data = deepcopy(self.observations)
        data['targets'][1] = deepcopy(data['targets'][0])
        with self.assertRaises(ValueError):
            self.run_score(data)

    def test_unlocatable_is_retained_not_imputed_as_pass(self):
        self.observations['targets'][1]['speech_end'] = {
            'status': 'unlocatable', 'earliest_seconds': None, 'latest_seconds': None}
        result = self.run_score()
        self.assertEqual(result['endpoint_classification_counts']['not_measured'], 1)
        self.assertEqual(result['endpoint_classification_counts']['within_tolerance'], 17)

    def test_wrong_hash_or_changed_audio_is_rejected(self):
        data = deepcopy(self.observations)
        data['manifest_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.run_score(data)
        (self.root / 'fake-audio.wav').write_bytes(b'changed synthetic bytes')
        with self.assertRaises(ValueError):
            self.run_score()

    def test_cli_requires_an_explicit_packet_root(self):
        output = self.root / 'result.json'
        result = subprocess.run([sys.executable, str(PATH), '--observations', str(self.path),
            '--output', str(output)], capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--root', result.stderr)
        self.assertFalse(output.exists())

    def test_cli_preserves_inputs_and_never_overwrites_a_result(self):
        write(self.path, self.observations)
        before = self.path.read_bytes()
        output = self.root / 'result.json'
        command = [sys.executable, str(PATH), '--root', str(self.root),
            '--observations', str(self.path), '--output', str(output)]
        first = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        result_bytes = output.read_bytes()
        second = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(output.read_bytes(), result_bytes)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
