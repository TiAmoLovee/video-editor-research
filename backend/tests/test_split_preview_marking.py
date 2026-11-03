"""Automatic clips and review copies must carry both marking forms or fail closed."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.tests.media_prerequisites import require_ass_marking
from clipforge.compliance import EXPORT_TAGS
from clipforge.decision import evaluation
from clipforge.decision.reviewed import file_hash
from clipforge.media import split


class FakeMark:
    ffmpeg = "frozen-ffmpeg"
    filter = "ass=filename=subtitles.ass:fontsdir=fonts"
    report = {"policy": "clipforge-visible-mark-v1", "text": "AI辅助剪辑"}

    def __init__(self, commands):
        self.commands = commands

    def run(self, command, *, timeout):
        self.commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")
        return "", ""


class MarkingWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")
        self.commands, self.requests = [], []

    @contextmanager
    def marking(self, **kwargs):
        self.requests.append(kwargs)
        yield FakeMark(self.commands)

    def raw(self, frames=901, tags=None):
        return {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 320,
                             "height": 180, "avg_frame_rate": "30/1", "nb_read_frames": str(frames),
                             "start_time": "0"}], "format": {"duration": str(frames/30), "tags": tags or {}}}

    def test_automatic_split_marks_every_clip_including_one_frame_tail(self):
        with patch.object(split, "probe_video", return_value=self.raw()), \
                patch.object(split, "read_frame_count", side_effect=[901, 900, 1]), \
                patch.object(split, "watermark_session", side_effect=self.marking), \
                patch.object(split, "run_tool", return_value=json.dumps({"format": {"tags": EXPORT_TAGS}})):
            result = split.split_video(str(self.source), str(self.root / "clips"))
        self.assertEqual([x["duration_frames"] for x in self.requests], [900, 1])
        self.assertEqual(len(result["render_markings"]), 2)
        for command in self.commands:
            self.assertEqual(command[command.index("-vf")+1], FakeMark.filter)
            self.assertIn("+faststart+use_metadata_tags", command)
            self.assertIn("clipforge_editing=AI-assisted-editing", command)

    def test_missing_font_or_missing_metadata_never_publishes_split_directory(self):
        for failure in ("font", "metadata"):
            target = self.root / failure
            mark = RuntimeError("missing configured font") if failure == "font" else self.marking
            with self.subTest(failure=failure), patch.object(split, "probe_video", return_value=self.raw()), \
                    patch.object(split, "read_frame_count", return_value=901), \
                    patch.object(split, "watermark_session", side_effect=mark), \
                    patch.object(split, "run_tool", return_value='{"format": {"tags": {}}}'):
                with self.assertRaises((ValueError, RuntimeError)):
                    split.split_video(str(self.source), str(target))
            self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob(".clipforge-split-*")))

    def plan(self):
        return {"batch_id": "frozen-id", "normalized_sha256": file_hash(self.source), "denominator": 1,
                "samples": [{"id": "S01", "file": "S01.mp4", "start_frame": 30,
                             "end_frame_exclusive": 90, "duration_seconds": 2.0}]}

    def test_review_marking_is_render_evidence_not_a_frozen_plan_change(self):
        plan = self.plan()
        with patch.object(evaluation, "probe", side_effect=[self.raw(90), self.raw(60, EXPORT_TAGS)]), \
                patch.object(evaluation, "watermark_session", side_effect=self.marking):
            result = evaluation.render_plan(plan, self.source, self.root / "review", "ffmpeg", "ffprobe")
        self.assertEqual(result["batch_id"], "frozen-id")
        self.assertEqual(self.requests[0]["duration_frames"], 60)
        self.assertEqual(self.requests[0]["profile"]["width"], 960)
        self.assertIn(FakeMark.filter, self.commands[0][self.commands[0].index("-vf")+1])
        self.assertEqual(result["renders"]["S01"]["marking"]["metadata"]["status"], "pass")
        saved = json.loads((self.root / "review/plan.json").read_text(encoding="utf-8"))
        self.assertNotIn("marking", saved)
        self.assertNotIn("renders", saved)

    def test_review_missing_font_or_metadata_never_publishes_manifest(self):
        for failure in ("font", "metadata"):
            output = self.root / failure
            mark = RuntimeError("missing configured font") if failure == "font" else self.marking
            with self.subTest(failure=failure), patch.object(evaluation, "probe", side_effect=[self.raw(90), self.raw(60)]), \
                    patch.object(evaluation, "watermark_session", side_effect=mark):
                with self.assertRaises((RuntimeError, ValueError)):
                    evaluation.render_plan(self.plan(), self.source, output, "ffmpeg", "ffprobe")
            self.assertFalse((output / "manifest.json").exists())


FFMPEG = shutil.which(os.environ.get("FFMPEG", "ffmpeg"))
FFPROBE = shutil.which(os.environ.get("FFPROBE", "ffprobe"))
FONT_READY = bool(os.environ.get("SUBTITLE_FONT_FILE") and os.environ.get("SUBTITLE_FONT_NAME"))


@unittest.skipUnless(FFMPEG and FFPROBE and FONT_READY,
                     "Real marking checks need configured FFmpeg/ffprobe and subtitle font; no downloads")
class RealSplitPreviewMarkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ass_marking(FFMPEG)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="split-marking-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 folder's [frames]"
        self.root.mkdir()
        self.source = self.root / "source.mp4"

    def make_source(self, frames):
        command = [FFMPEG, "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30",
                   "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                   "-fps_mode", "cfr", str(self.source)]
        subprocess.run(command, check=True, capture_output=True, timeout=60)

    def assert_marked_ends(self, output, frames):
        info = evaluation.probe(output, FFPROBE)
        self.assertEqual({k: info["format"]["tags"].get(k) for k in EXPORT_TAGS}, EXPORT_TAGS)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual(int(video["nb_read_frames"]), frames)
        width, height = video["width"], video["height"]
        indices = sorted({0, frames-1})
        expression = "+".join(f"eq(n,{n})" for n in indices)
        command = [FFMPEG, "-v", "error", "-nostdin", "-i", str(output), "-vf", f"select='{expression}'",
                   "-fps_mode", "passthrough", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
        data = subprocess.run(command, check=True, capture_output=True, timeout=30).stdout
        self.assertEqual(len(data), len(indices)*width*height)
        for index in range(len(indices)):
            frame = data[index*width*height:(index+1)*width*height]
            right_top = [value for y in range(height//3) for value in frame[y*width+width//2:(y+1)*width]]
            # Small CJK glyphs are antialiased: an 8px label need not contain
            # pure-white pixels, but must be visibly brighter than black.
            self.assertGreater(sum(value > 100 for value in right_top), 10)
            lower_left = [value for y in range(height//2, height) for value in frame[y*width:y*width+width//2]]
            self.assertLess(max(lower_left), 20)

    def test_split_first_last_and_one_frame_tail_have_visible_pixels_and_tags(self):
        self.make_source(901)
        source_hash = file_hash(self.source)
        result = split.split_video(str(self.source), str(self.root / "clips"), FFMPEG, FFPROBE, 120)
        self.assertEqual([x["frame_count"] for x in result["clips"]], [900, 1])
        for item in result["clips"]:
            self.assert_marked_ends(self.root / "clips" / item["file"], item["frame_count"])
        self.assertEqual(file_hash(self.source), source_hash)

    def test_review_first_last_have_visible_pixels_and_tags(self):
        self.make_source(90)
        plan = {"normalized_sha256": file_hash(self.source), "denominator": 1,
                "samples": [{"id": "S01", "file": "S01.mp4", "start_frame": 30,
                             "end_frame_exclusive": 90, "duration_seconds": 2.0}]}
        result = evaluation.render_plan(plan, self.source, self.root / "review", FFMPEG, FFPROBE)
        self.assert_marked_ends(self.root / "review/S01.mp4", 60)
        self.assertEqual(result["renders"]["S01"]["marking"]["metadata"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
