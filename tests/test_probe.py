"""使用标准库 unittest 验证元数据转换，不依赖 FFprobe 或真实素材。"""

import unittest

from probe import normalize_metadata


class MetadataTests(unittest.TestCase):
    def sample(self):
        return {
            "format": {"duration": "41.266667", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "hevc",
                 "width": 960, "height": 600, "avg_frame_rate": "30/1"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac",
                 "sample_rate": "44100", "channels": 1},
            ],
        }

    def test_numbers_are_converted(self):
        meta = normalize_metadata(self.sample(), "sample.mp4")
        self.assertEqual(meta["duration_seconds"], 41.266667)
        self.assertEqual(meta["video"]["width"], 960)
        self.assertEqual(meta["video"]["avg_fps"], 30.0)
        self.assertEqual(meta["audio"]["sample_rate_hz"], 44100)

    def test_silent_video_has_null_audio(self):
        raw = self.sample()
        raw["streams"] = raw["streams"][:1]
        self.assertIsNone(normalize_metadata(raw, "silent.mp4")["audio"])

    def test_unknown_rate_is_not_zero(self):
        for rate in ("0/0", "N/A", "0/1", None):
            with self.subTest(rate=rate):
                raw = self.sample()
                raw["streams"][0]["avg_frame_rate"] = rate
                meta = normalize_metadata(raw, "sample.mp4")
                self.assertIsNone(meta["video"]["avg_fps"])
                self.assertIsNone(meta["video"]["avg_frame_rate"])

    def test_fractional_rate_is_preserved(self):
        raw = self.sample()
        raw["streams"][0]["avg_frame_rate"] = "30000/1001"
        meta = normalize_metadata(raw, "sample.mp4")
        self.assertEqual(meta["video"]["avg_frame_rate"], "30000/1001")
        self.assertAlmostEqual(meta["video"]["avg_fps"], 29.97002997)

    def test_unknown_duration_remains_null(self):
        for duration in (None, "N/A", "nan", "inf", "-1", "0"):
            with self.subTest(duration=duration):
                raw = self.sample()
                raw["format"]["duration"] = duration
                self.assertIsNone(normalize_metadata(raw, "sample.mp4")["duration_seconds"])

    def test_cover_image_is_skipped(self):
        raw = self.sample()
        raw["streams"].insert(0, {"index": 9, "codec_type": "video",
                                  "disposition": {"attached_pic": 1}})
        self.assertEqual(normalize_metadata(raw, "sample.mp4")["video"]["stream_index"], 0)

    def test_audio_only_is_rejected(self):
        raw = self.sample()
        raw["streams"] = raw["streams"][1:]
        with self.assertRaises(ValueError):
            normalize_metadata(raw, "audio.m4a")


if __name__ == "__main__":
    unittest.main()
