import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace as Object
import unittest
from unittest.mock import patch

from clipforge.analysis.asr import transcribe_video
from clipforge.analysis.chinese import simplify_words
from clipforge.analysis.speech_gate import SPEECH_GATE_PARAMETERS


class ChineseTests(unittest.TestCase):
    def test_phrase_context_spans_words_and_preserves_alignment(self):
        words = [{"id": f"w{i}", "start": i, "end": i + .5, "text": t, "probability": .8}
                 for i, t in enumerate(["乾", "隆", "的頭髮", "與音樂", " OpenAI 2026!"])]
        original = copy.deepcopy(words)
        result = simplify_words(words)
        self.assertEqual("".join(w["text"] for w in result), "乾隆的头发与音乐 OpenAI 2026!")
        self.assertEqual(words, original)
        for old, new in zip(words, result):
            self.assertEqual({k: v for k, v in old.items() if k != "text"},
                             {k: v for k, v in new.items() if k != "text"})

    def test_empty_and_english_are_unchanged(self):
        self.assertEqual(simplify_words([]), [])
        self.assertEqual(simplify_words([{"text": " Hello world!"}]), [{"text": " Hello world!"}])

    def test_length_changing_dictionary_cannot_corrupt_alignment(self):
        with patch("clipforge.analysis.chinese._converter") as converter:
            converter.return_value.convert.return_value = "different length"
            with self.assertRaisesRegex(ValueError, "时间映射"):
                simplify_words([{"text": "字"}])


class SpeechGateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source = Path(folder.name) / "video.mp4"
        self.source.write_bytes(b"fixture")
        raw = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1"},
                           {"index": 1, "codec_type": "audio", "codec_name": "aac"}]}
        for name, value in [("probe_video", raw), ("read_frame_count", 300),
                            ("extract_pcm", None), ("pcm_has_signal", True)]:
            patcher = patch(f"clipforge.analysis.asr.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nonzero_audio_without_speech_does_not_load_whisper(self):
        gate = {"no_speech_skipped": True, "speech_intervals": [], "retained_seconds": 0}
        with patch("clipforge.analysis.asr.prepare_speech", return_value=([], gate)), \
             patch("clipforge.analysis.asr._load_model") as load:
            result = transcribe_video(self.source)
        load.assert_not_called()
        self.assertTrue(result["media"]["has_audio"])
        self.assertEqual(result["analyzer"]["status"], "ok")
        self.assertEqual((result["words"], result["sentences"]), ([], []))
        self.assertFalse(result["analyzer"]["parameters"]["digital_silence_skipped"])
        self.assertTrue(result["analyzer"]["parameters"]["speech_gate"]["no_speech_skipped"])

    def test_filtered_transcription_retains_original_timeline_and_raw_text(self):
        word = Object(start=5, end=6, word="音樂與講話", probability=.9)
        segment = Object(start=5, end=6, text=word.word, words=[word])
        gate = {"no_speech_skipped": False, "speech_intervals": [{"start": 4.8, "end": 6.2}]}
        audio = object()
        with patch("clipforge.analysis.asr.prepare_speech", return_value=(audio, gate)), \
             patch("clipforge.analysis.asr._load_model") as load:
            model = load.return_value = (Object(), {})
            from unittest.mock import Mock
            model[0].transcribe = Mock(return_value=(iter([segment]), Object(language="zh", language_probability=.9)))
            result = transcribe_video(self.source)
        args, kwargs = model[0].transcribe.call_args
        self.assertIs(args[0], audio)
        self.assertTrue(kwargs["vad_filter"])
        self.assertEqual(kwargs["vad_parameters"], SPEECH_GATE_PARAMETERS)
        self.assertEqual(result["words"][0]["text"], "音乐与讲话")
        self.assertEqual((result["words"][0]["start"], result["words"][0]["end"]), (5, 6))
        self.assertEqual(result["sentences"][0]["text"], "音乐与讲话")
        self.assertEqual(result["raw_segments"][0]["text"], "音樂與講話")
        self.assertEqual(result["raw_segments"][0]["words"][0]["text"], "音樂與講話")

    def test_speech_gate_failure_is_not_reported_as_successful_empty_text(self):
        with patch("clipforge.analysis.asr.prepare_speech", side_effect=RuntimeError("VAD unavailable")):
            with self.assertRaisesRegex(RuntimeError, "VAD unavailable"):
                transcribe_video(self.source)


if __name__ == "__main__":
    unittest.main()
