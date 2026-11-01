"""Optional GPU settings, truthful metadata and model/result cache isolation; no inference."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as Object
import unittest
from unittest.mock import Mock, patch

import numpy as np

from clipforge.analysis.asr import _load_model, transcribe_video
from clipforge.analysis.runtime import runtime_settings, validate_cached_runtime
from clipforge.services import result_cache


class AsrRuntimeTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'CLIPFORGE_ASR_MODEL': 'base', 'CLIPFORGE_ASR_DEVICE': '',
                                     'CLIPFORGE_ASR_COMPUTE_TYPE': ''})
        env.start()
        self.addCleanup(env.stop)
        _load_model.cache_clear()
        self.addCleanup(_load_model.cache_clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'normalized.mp4'
        self.source.write_bytes(b'synthetic placeholder')

    def test_default_runtime_is_unchanged_cpu_int8_four_threads(self):
        self.assertEqual(runtime_settings(), {'device': 'cpu', 'compute_type': 'int8',
                                               'cpu_threads': 4, 'num_workers': 1})
        with patch('clipforge.analysis.asr.verify_model', return_value={}), \
             patch('faster_whisper.WhisperModel') as model:
            _load_model('fixed-directory', 'base')
        model.assert_called_once_with('fixed-directory', device='cpu', compute_type='int8',
                                      cpu_threads=4, num_workers=1, local_files_only=True)

    def test_cuda_default_and_explicit_compute_modes(self):
        with patch.dict(os.environ, {'CLIPFORGE_ASR_DEVICE': 'cuda'}):
            self.assertEqual(runtime_settings()['compute_type'], 'float16')
        self.assertEqual(runtime_settings(device='cuda', compute_type='int8_float16')['compute_type'], 'int8_float16')
        self.assertEqual(runtime_settings(device='cpu', compute_type='float32')['compute_type'], 'float32')

    def test_invalid_runtime_fails_before_model_or_video_access(self):
        for device, compute in (('auto', ''), ('gpu', ''), ('cpu', 'float16'), ('cuda', 'int8'),
                                ('cuda', 'float32'), ('cpu', 'unknown')):
            with self.subTest(device=device, compute=compute), \
                 patch.dict(os.environ, {'CLIPFORGE_ASR_DEVICE': device, 'CLIPFORGE_ASR_COMPUTE_TYPE': compute}), \
                 patch('clipforge.analysis.asr.probe_video') as probe:
                with self.assertRaises(ValueError):
                    transcribe_video('missing.mp4')
                probe.assert_not_called()

    def test_model_cache_key_separates_device_and_precision(self):
        first, second, third = object(), object(), object()
        with patch('clipforge.analysis.asr.verify_model', return_value={}), \
             patch('faster_whisper.WhisperModel', side_effect=[first, second, third]) as model:
            self.assertIs(_load_model('same', 'base')[0], first)
            self.assertIs(_load_model('same', 'base')[0], first)
            self.assertIs(_load_model('same', 'base', 'cuda', 'float16')[0], second)
            self.assertIs(_load_model('same', 'base', 'cuda', 'float16')[0], second)
            self.assertIs(_load_model('same', 'base', 'cuda', 'int8_float16')[0], third)
        self.assertEqual(model.call_count, 3)
        self.assertEqual([call.kwargs['device'] for call in model.call_args_list], ['cpu', 'cuda', 'cuda'])

    def test_cuda_load_error_is_not_silently_replaced_with_cpu(self):
        with patch('clipforge.analysis.asr.verify_model', return_value={}), \
             patch('faster_whisper.WhisperModel', side_effect=RuntimeError('CUDA library unavailable')) as model:
            with self.assertRaisesRegex(RuntimeError, 'CUDA library unavailable'):
                _load_model('same', 'base', 'cuda', 'float16')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.kwargs['device'], 'cuda')

    def test_transcription_uses_selected_runtime_and_records_same_metadata(self):
        raw = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264', 'avg_frame_rate': '30/1'},
                           {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac'}]}
        word = Object(start=.1, end=.3, word='sample', probability=.9)
        segment = Object(start=.1, end=.3, text='sample', words=[word])
        gate = {'no_speech_skipped': False, 'speech_intervals': [{'start': 0, 'end': .5}], 'retained_seconds': .5}
        for device, compute in (('cpu', 'int8'), ('cuda', 'float16')):
            model = Mock()
            model.transcribe.return_value = (iter([segment]), Object(language='en', language_probability=.9))
            with self.subTest(device=device), \
                 patch.dict(os.environ, {'CLIPFORGE_ASR_DEVICE': device, 'CLIPFORGE_ASR_COMPUTE_TYPE': compute}), \
                 patch('clipforge.analysis.asr.probe_video', return_value=raw), \
                 patch('clipforge.analysis.asr.read_frame_count', return_value=30), \
                 patch('clipforge.analysis.asr.extract_pcm'), \
                 patch('clipforge.analysis.asr.pcm_has_signal', return_value=True), \
                 patch('clipforge.analysis.asr.prepare_speech', return_value=(np.zeros(16000), gate)), \
                 patch('clipforge.analysis.asr._load_model', return_value=(model, {})) as load, \
                 patch('clipforge.analysis.asr.recover_word_coverage',
                       side_effect=lambda audio, model, words, gate, duration, **kwargs: (words, {})):
                actual = transcribe_video(self.source)
            parameters = actual['analyzer']['parameters']
            self.assertEqual((parameters['device'], parameters['compute_type']), (device, compute))
            self.assertEqual(len(load.call_args.args), 2 if device == 'cpu' else 4)
            if device == 'cuda':
                self.assertEqual(load.call_args.args[2:], ('cuda', 'float16'))
            model.transcribe.assert_called_once()

    def test_result_fingerprint_changes_for_device_and_compute_type(self):
        with patch.object(result_cache, 'verify_model', return_value={'revision': 'fixed'}), \
             patch.object(result_cache, 'version', return_value='fixed'), \
             patch.object(result_cache.subprocess, 'run', return_value=Object(stdout='fixed-tool')):
            cpu_key, cpu = result_cache.fingerprint(self.source, None, 'ffmpeg', 'ffprobe')
            with patch.dict(os.environ, {'CLIPFORGE_ASR_DEVICE': 'cuda'}):
                gpu_key, gpu = result_cache.fingerprint(self.source, None, 'ffmpeg', 'ffprobe')
            with patch.dict(os.environ, {'CLIPFORGE_ASR_DEVICE': 'cpu', 'CLIPFORGE_ASR_COMPUTE_TYPE': 'float32'}):
                precision_key, _ = result_cache.fingerprint(self.source, None, 'ffmpeg', 'ffprobe')
        self.assertEqual(len({cpu_key, gpu_key, precision_key}), 3)
        self.assertEqual(cpu['asr_runtime']['device'], 'cpu')
        self.assertEqual(gpu['asr_runtime']['device'], 'cuda')
        self.assertEqual(gpu['asr_runtime']['compute_type'], 'float16')

    def test_cached_payload_runtime_must_match_requested_fingerprint(self):
        expected = runtime_settings(device='cuda', compute_type='float16')
        validate_cached_runtime(dict(expected), expected)
        for changed in ({**expected, 'device': 'cpu'}, {**expected, 'compute_type': 'int8_float16'},
                        {**expected, 'num_workers': True}, {}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_cached_runtime(changed, expected)
        (self.root / 'asr.json').write_text(json.dumps({'analyzer': {'parameters': runtime_settings()}}), encoding='utf-8')
        with patch.object(result_cache, 'combine_analysis') as combine:
            with self.assertRaisesRegex(ValueError, '缓存 ASR'):
                result_cache.validate_bundle(self.root, self.source, {}, {'shot_options': None, 'asr_runtime': expected})
        combine.assert_not_called()


if __name__ == '__main__':
    unittest.main()
