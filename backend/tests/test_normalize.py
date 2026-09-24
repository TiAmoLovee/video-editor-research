"""检查转码的输出约束和文件保护；不启动 FFmpeg。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipforge.media.normalize import normalize_video, validate_output


class NormalizationTests(unittest.TestCase):
    def valid_metadata(self):
        return {"video": {"codec": "h264", "avg_frame_rate": "30/1"},
                "audio": {"codec": "aac"}, "duration_seconds": 2.0}

    def test_accepts_expected_output(self):
        validate_output(self.valid_metadata(), had_audio=True)

    def test_rejects_wrong_codec(self):
        metadata = self.valid_metadata()
        metadata["video"]["codec"] = "hevc"
        with self.assertRaises(ValueError):
            validate_output(metadata, had_audio=True)

    def test_rejects_wrong_rate(self):
        metadata = self.valid_metadata()
        metadata["video"]["avg_frame_rate"] = "30000/1001"
        with self.assertRaises(ValueError):
            validate_output(metadata, had_audio=True)

    def test_rejects_lost_audio(self):
        metadata = self.valid_metadata()
        metadata["audio"] = None
        with self.assertRaises(ValueError):
            validate_output(metadata, had_audio=True)

    def test_accepts_silent_video(self):
        metadata = self.valid_metadata()
        metadata["audio"] = None
        validate_output(metadata, had_audio=False)

    def test_never_overwrites_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.mp4"
            source.write_bytes(b"original")
            with self.assertRaises(ValueError), patch("clipforge.media.normalize.subprocess.run") as run:
                normalize_video(str(source), str(source))
            run.assert_not_called()
            self.assertEqual(source.read_bytes(), b"original")

    def test_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "input.mp4", Path(folder) / "output.mp4"
            source.write_bytes(b"original")
            target.write_bytes(b"previous result")
            with self.assertRaises(ValueError), patch("clipforge.media.normalize.subprocess.run") as run:
                normalize_video(str(source), str(target))
            run.assert_not_called()
            self.assertEqual(target.read_bytes(), b"previous result")


if __name__ == "__main__":
    unittest.main()
