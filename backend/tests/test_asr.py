import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as Object
import unittest
import wave
from unittest.mock import patch

from clipforge.analysis.asr import collect_words, pcm_has_signal, transcribe_video
from clipforge.analysis.combine import combine_analysis
from clipforge.analysis.model import download_model, verify_model
from clipforge.analysis.sentences import split_sentences


def segment(*words, text="示例"):
    return Object(start=0, end=1, text=text, words=[Object(start=a, end=b, word=t, probability=p)
                                                 for a, b, t, p in words])


class WordSentenceTests(unittest.TestCase):
    def test_chinese_punctuation_and_quotes_preserve_word_references(self):
        words, _, _ = collect_words([segment((0, .2, "你好", .9), (.2, .4, "世界。", .8),
                                             (.5, .8, "“开始！”", .7))], 1)
        sentences = split_sentences(words)
        self.assertEqual([s["text"] for s in sentences], ["你好世界。", "“开始！”"])
        self.assertEqual([s["word_ids"] for s in sentences], [["w000001", "w000002"], ["w000003"]])
        self.assertEqual([(s["start"], s["end"]) for s in sentences], [(0, .4), (.5, .8)])

    def test_english_spacing_is_preserved(self):
        words, _, _ = collect_words([segment((0, .2, " Hello", .9), (.2, .4, " world.", .9))], 1)
        self.assertEqual(split_sentences(words)[0]["text"], "Hello world.")

    def test_gap_length_and_character_fallbacks_do_not_lose_words(self):
        words = [{"id": str(i), "start": i * 2, "end": i * 2 + .5,
                  "text": "无标点", "probability": .9} for i in range(8)]
        self.assertEqual(len(split_sentences(words)), 8)
        for item in words:
            item["end"] = item["start"] + 2
        self.assertEqual(len(split_sentences(words)), 2)  # 15 秒兜底
        for i, item in enumerate(words):
            item.update(start=i / 10, end=(i + 1) / 10, text="字" * 20)
        result = split_sentences(words)
        self.assertEqual(len(result), 4)
        self.assertEqual([x for s in result for x in s["word_ids"]], [w["id"] for w in words])

    def test_zero_duration_tokens_are_merged_without_fabricated_timestamps(self):
        words, raw, diagnostic = collect_words([segment((0, 0, "前", .9), (0, .2, "词", .8),
                                                       (.2, .2, "。", .7))], 1)
        self.assertEqual(words, [{"id": "w000001", "start": 0, "end": .2, "text": "前词。", "probability": None}])
        self.assertEqual(len(raw[0]["words"]), 3)
        self.assertEqual(diagnostic["zero_duration_words_merged"], 2)

    def test_only_untimed_text_fails(self):
        with self.assertRaisesRegex(ValueError, "零时长"):
            collect_words([segment((0, 0, "文字", .9))], 1)

    def test_missing_word_timestamps_fail(self):
        with self.assertRaisesRegex(ValueError, "词级时间"):
            collect_words([segment()], 1)

    def test_minor_boundary_rounding_clamps_and_large_errors_fail(self):
        words, _, diagnostic = collect_words([segment((0, 1.02, "词", .8))], 1)
        self.assertEqual(words[0]["end"], 1)
        self.assertEqual(diagnostic["boundary_words_clamped"], 1)
        for a, b, p in [(-1, .2, .9), (0, 2, .9), (.5, .2, .9), (float("nan"), .2, .9),
                        (0, .2, float("inf")), (0, .2, 1.1)]:
            with self.subTest(a=a, b=b, p=p), self.assertRaises(ValueError):
                collect_words([segment((a, b, "词", p))], 1)

    def test_unsorted_words_fail(self):
        with self.assertRaisesRegex(ValueError, "顺序"):
            collect_words([segment((.5, .6, "后", .8), (.1, .2, "前", .8))], 1)

    def test_generator_failure_propagates(self):
        def failing():
            yield segment((0, .2, "词", .9))
            raise RuntimeError("inference failed")
        with self.assertRaisesRegex(RuntimeError, "inference failed"):
            collect_words(failing(), 1)

    def test_empty_speech_has_no_fake_words_or_sentences(self):
        words, raw, _ = collect_words([], 1)
        self.assertEqual((words, raw, split_sentences(words)), ([], [], []))


class ModelAndCombineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")

    def test_missing_and_corrupt_model_fail_without_download(self):
        with patch("clipforge.analysis.model.urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "未准备好"):
                verify_model(self.root)
            (self.root / "config.json").write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "校验失败"):
                download_model(self.root)
        network.assert_not_called()

    def test_model_file_digest_is_verified(self):
        (self.root / "model.bin").write_bytes(b"weights")
        with patch("clipforge.analysis.model.MODEL_HASHES", {"model.bin": hashlib.sha256(b"weights").hexdigest()}):
            self.assertEqual(verify_model(self.root)["sha256"]["model.bin"], hashlib.sha256(b"weights").hexdigest())

    def test_digital_silence_guard_keeps_even_quiet_nonzero_signal(self):
        pcm = self.root / "audio.wav"
        for data, expected in [(b"\0\0" * 160, False), (b"\0\0" * 159 + b"\1\0", True)]:
            with wave.open(str(pcm), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(data)
            self.assertEqual(pcm_has_signal(pcm, 160), expected)
        pcm.write_bytes(pcm.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "截断"):
            pcm_has_signal(pcm, 160)

    def test_silent_audio_retains_ok_status_without_inference(self):
        raw = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1"},
                           {"index": 1, "codec_type": "audio", "codec_name": "aac"}]}
        with patch("clipforge.analysis.asr.probe_video", return_value=raw), \
             patch("clipforge.analysis.asr.read_frame_count", return_value=30), \
             patch("clipforge.analysis.asr.extract_pcm"), \
             patch("clipforge.analysis.asr.pcm_has_signal", return_value=False), \
             patch("clipforge.analysis.asr._load_model") as model:
            result = transcribe_video(self.source)
        model.assert_not_called()
        self.assertEqual(result["analyzer"]["status"], "ok")
        self.assertTrue(result["analyzer"]["parameters"]["digital_silence_skipped"])
        self.assertEqual(result["words"], [])

    def test_no_audio_does_not_load_model_or_extract_audio(self):
        raw = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "avg_frame_rate": "30/1"}]}
        with patch("clipforge.analysis.asr.probe_video", return_value=raw), \
             patch("clipforge.analysis.asr.read_frame_count", return_value=30), \
             patch("clipforge.analysis.asr._load_model") as model, \
             patch("clipforge.analysis.asr.extract_pcm") as extract:
            result = transcribe_video(self.source)
        model.assert_not_called()
        extract.assert_not_called()
        self.assertEqual(result["analyzer"]["status"], "no_audio")
        self.assertEqual((result["words"], result["sentences"]), ([], []))

    def components(self):
        sample = json.loads((Path(__file__).resolve().parents[2] / "docs/samples/analysis.example.json").read_text(encoding="utf-8"))
        meta = {**sample["media"], "total_frames": 300, "fps": 30}
        shots = {"artifact_kind": "shot_analysis", "result_kind": "measured", "media": meta.copy(),
                 "analyzer": sample["analyzers"]["shots"], "shots": sample["shots"]}
        vad = {"artifact_kind": "vad_analysis", "result_kind": "measured", "media": meta.copy(),
               "analyzer": sample["analyzers"]["vad"], "speech": sample["speech"], "silence": sample["silence"]}
        asr = {"artifact_kind": "asr_analysis", "result_kind": "measured", "media": meta.copy(),
               "analyzer": sample["analyzers"]["asr"], "words": sample["words"], "sentences": sample["sentences"]}
        return shots, vad, asr

    def test_combine_validates_schema_and_hashes_original_source(self):
        result = combine_analysis(self.source, *self.components())
        self.assertEqual(result["media"]["source_sha256"], hashlib.sha256(b"source").hexdigest())
        self.assertEqual(result["result_kind"], "measured")

    def test_combine_rejects_identity_and_timeline_mismatches(self):
        for key, value in [("normalized_sha256", "a" * 64), ("duration_seconds", 11),
                           ("has_audio", False), ("total_frames", 299)]:
            items = copy.deepcopy(self.components())
            items[2]["media"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                combine_analysis(self.source, *items)

    def test_combine_rejects_synthetic_and_incomplete_sentence_references(self):
        items = self.components()
        items[2]["result_kind"] = "synthetic_example"
        with self.assertRaises(ValueError):
            combine_analysis(self.source, *items)
        items[2]["result_kind"] = "measured"
        items[2]["sentences"] = []
        with self.assertRaises(ValueError):
            combine_analysis(self.source, *items)


if __name__ == "__main__":
    unittest.main()
