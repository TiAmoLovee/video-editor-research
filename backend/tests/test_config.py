import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipforge.config import media_tool
from clipforge.media.probe import probe_video


class ToolConfigTests(unittest.TestCase):
    def test_explicit_overrides_environment(self):
        with patch.dict(os.environ, {"FFMPEG": "custom", "FFMPEG_PATH": "legacy"}, clear=True):
            self.assertEqual(media_tool("ffmpeg", "explicit"), "explicit")
            self.assertEqual(media_tool("ffmpeg"), "custom")

    def test_legacy_and_path_fallback(self):
        with patch.dict(os.environ, {"FFPROBE_PATH": "legacy"}, clear=True):
            self.assertEqual(media_tool("ffprobe"), "legacy")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(media_tool("ffprobe"), "ffprobe")

    def test_environment_is_read_at_call_time(self):
        with patch.dict(os.environ, {"FFPROBE": "first"}, clear=True):
            self.assertEqual(media_tool("ffprobe"), "first")
            os.environ["FFPROBE"] = "second"
            self.assertEqual(media_tool("ffprobe"), "second")

    def test_probe_uses_configured_executable_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'video.mp4'
            source.write_bytes(b'fixture')
            with patch.dict(os.environ, {"FFPROBE": "some folder/ffprobe.exe"}), \
                 patch('clipforge.media.probe.subprocess.run') as run:
                run.return_value.stdout = '{"streams":[{"codec_type":"video"}]}'
                probe_video(str(source))
            self.assertEqual(run.call_args.args[0][0], 'some folder/ffprobe.exe')

    def test_unsupported_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            media_tool('other')
