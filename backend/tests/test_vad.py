import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from clipforge.analysis.vad import SAMPLE_RATE, classify_pcm, detect_speech, extract_pcm


class VadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "test.wav"

    def pcm(self, samples, rate=SAMPLE_RATE, channels=1):
        with wave.open(str(self.path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(b"\0\0" * samples * channels)

    def test_real_webrtc_vad_silence_and_partial_last_frame(self):
        duration = 1 / 30
        self.pcm(math.ceil(duration * SAMPLE_RATE))
        speech, silence = classify_pcm(self.path, duration)
        self.assertEqual(speech, [])
        self.assertEqual(silence, [{"start": 0, "end": duration}])

    def test_labels_merge_and_cover_timeline_without_gaps(self):
        self.pcm(2400)
        with patch("webrtcvad.Vad") as factory:
            factory.return_value.is_speech.side_effect = [False, True, True, False, True]
            speech, silence = classify_pcm(self.path, .15)
        self.assertEqual(speech, [{"start": .03, "end": .09}, {"start": .12, "end": .15}])
        self.assertEqual(silence, [{"start": 0, "end": .03}, {"start": .09, "end": .12}])

    def test_partial_speech_frame_is_padded_and_clamped(self):
        duration = 1 / 30
        self.pcm(math.ceil(duration * SAMPLE_RATE))
        with patch("webrtcvad.Vad") as factory:
            detector = factory.return_value
            detector.is_speech.return_value = True
            speech, silence = classify_pcm(self.path, duration)
        self.assertEqual(speech, [{"start": 0, "end": duration}])
        self.assertEqual(silence, [])
        self.assertEqual([len(call.args[0]) for call in detector.is_speech.call_args_list], [960, 960])

    def test_all_supported_frame_lengths(self):
        self.pcm(1600)
        for frame_ms in (10, 20, 30):
            with self.subTest(frame_ms=frame_ms):
                self.assertEqual(classify_pcm(self.path, .1, frame_ms=frame_ms),
                                 ([], [{"start": 0, "end": .1}]))

    def test_wrong_format_rejected(self):
        for rate, channels in [(8000, 1), (16000, 2)]:
            with self.subTest(rate=rate, channels=channels):
                self.pcm(1600, rate, channels)
                with self.assertRaisesRegex(ValueError, "16 kHz"):
                    classify_pcm(self.path, .1)

    def test_wrong_length_and_truncated_data_rejected(self):
        self.pcm(100)
        with self.assertRaisesRegex(ValueError, "采样数"):
            classify_pcm(self.path, .1)
        self.pcm(1600)
        self.path.write_bytes(self.path.read_bytes()[:-20])
        with self.assertRaisesRegex(ValueError, "截断"):
            classify_pcm(self.path, .1)

    def test_invalid_options_fail_before_media_access(self):
        for options in [{"mode": -1}, {"mode": True}, {"mode": 4}, {"frame_ms": 15}, {"frame_ms": False}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                detect_speech("missing.mp4", **options)

    def test_no_audio_is_distinct_from_silence_and_does_not_extract(self):
        self.path.write_bytes(b"video placeholder")
        raw = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264",
                            "avg_frame_rate": "30/1", "start_time": "0"}]}
        with patch("clipforge.analysis.vad.probe_video", return_value=raw), \
             patch("clipforge.analysis.vad.read_frame_count", return_value=30), \
             patch("clipforge.analysis.vad.extract_pcm") as extract:
            result = detect_speech(self.path)
        extract.assert_not_called()
        self.assertEqual(result["analyzer"]["status"], "no_audio")
        self.assertFalse(result["media"]["has_audio"])
        self.assertEqual((result["speech"], result["silence"]), ([], []))

    def test_detector_error_is_not_replaced_with_empty_success(self):
        self.pcm(1600)
        with patch("webrtcvad.Vad") as factory:
            factory.return_value.is_speech.side_effect = RuntimeError("detector failed")
            with self.assertRaisesRegex(RuntimeError, "detector failed"):
                classify_pcm(self.path, .1)

    def test_extraction_errors_fail_explicitly(self):
        for error in [FileNotFoundError(), subprocess.TimeoutExpired("ffmpeg", 1),
                      subprocess.CalledProcessError(1, "ffmpeg", stderr="bad audio")]:
            with self.subTest(error=error), patch("clipforge.analysis.vad.subprocess.run", side_effect=error):
                with self.assertRaises(RuntimeError):
                    extract_pcm("video.mp4", self.path, 1, 0, 1600, "ffmpeg")


if __name__ == "__main__":
    unittest.main()
