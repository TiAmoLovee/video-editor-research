"""New export metadata must survive real muxing; it never proves visible pixels."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from backend.tests.test_render import RenderHarness, edl_fixture, source_fixture
from backend.tests.media_prerequisites import require_ass_marking
from clipforge.compliance import EXPORT_TAGS, inspect_metadata, metadata_arguments, require_metadata
from clipforge.editing import render
from clipforge.editing.manifest import build_media_manifest
from tools.week8 import check_export


class MetadataContractTests(unittest.TestCase):
    def test_missing_wrong_and_legacy_tags_are_not_current_marking(self):
        for tags in (None, {}, {"comment": "ClipForge EDL renderer 0.1.0", "editing": "ClipForge"},
                     {**EXPORT_TAGS, "clipforge_editing": "AI-generated"},
                     {**EXPORT_TAGS, "clipforge_marking_version": 1}):
            with self.subTest(tags=tags):
                self.assertEqual(inspect_metadata(tags)["status"], "fail")
                with self.assertRaisesRegex(ValueError, "AI-assisted-editing"):
                    require_metadata(tags)

    def test_missing_or_changed_muxed_tag_does_not_publish_new_edl_output(self):
        for key in EXPORT_TAGS:
            for mode in ("remove", "change"):
                with self.subTest(key=key, mode=mode), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    project = edl_fixture([source_fixture(root)])
                    target = root / "output.mp4"
                    harness = RenderHarness(project, root, target)
                    original_tool = harness.tool

                    def corrupted_tag(command, timeout):
                        result = original_tool(command, timeout)
                        if "-filter_complex_script" not in command:
                            data = json.loads(result)
                            if mode == "remove":
                                del data["format"]["tags"][key]
                            else:
                                data["format"]["tags"][key] = "unexpected"
                            result = json.dumps(data)
                        return result

                    harness.tool = corrupted_tag
                    with ExitStack() as stack:
                        harness.install(stack)
                        with self.assertRaisesRegex(ValueError, "AI-assisted-editing"):
                            render.render_edl(project, root, target)
                    self.assertFalse(target.exists())
                    self.assertFalse(list(root.glob(".clipforge-render-*")))

    def test_inspection_does_not_mutate_observed_metadata(self):
        tags = {**EXPORT_TAGS, "unrelated_private_tag": "not included in report"}
        before = deepcopy(tags)
        result = inspect_metadata(tags)
        self.assertEqual(tags, before)
        self.assertEqual(result["status"], "pass")
        self.assertNotIn("unrelated_private_tag", result["observed"])
        result["expected"]["clipforge_tool"] = "changed by caller"
        self.assertEqual(EXPORT_TAGS["clipforge_tool"], "ClipForge")


FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("FFPROBE") or shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "real FFmpeg and FFprobe are required")
class MetadataMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def video(self, name, tags=False, audio=False):
        target = self.root / name
        command = [FFMPEG, "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                   "color=c=navy:s=320x180:r=30:d=0.5"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.5",
                        "-c:a", "aac", "-ac", "2"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                    "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
        if tags:
            command += metadata_arguments()
        command += ["-movflags", "+faststart+use_metadata_tags", str(target)]
        subprocess.run(command, capture_output=True, check=True, timeout=30)
        return target

    def test_metadata_only_plain_pixels_are_not_certified_as_visible_label(self):
        target = self.video("plain-colour-with-tags.mp4", tags=True)
        result = check_export.inspect_export(target, FFPROBE)
        self.assertTrue(result["metadata_check_passed"])
        self.assertEqual(result["scope"], "metadata-only")
        self.assertEqual(result["visible_label"]["status"], "not_checked")
        self.assertIs(result["legal_compliance_assessed"], False)

    def test_real_unmarked_video_returns_failure_and_cli_does_not_overwrite(self):
        target = self.video("unmarked.mp4")
        with redirect_stdout(io.StringIO()) as output:
            result = check_export.main([str(target), "--ffprobe", FFPROBE])
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output.getvalue())["metadata_check_passed"])
        report = self.root / "existing.json"
        report.write_bytes(b"existing report")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(check_export.main([str(target), "--ffprobe", FFPROBE,
                                               "--output", str(report)]), 2)
        self.assertEqual(report.read_bytes(), b"existing report")

    def test_real_edl_exports_with_and_without_audio_have_verified_metadata(self):
        require_ass_marking(FFMPEG)
        for audio in (False, True):
            with self.subTest(audio=audio):
                source = self.video(f"source-{audio}.mp4", audio=audio)
                measured = build_media_manifest(source, source_id="s0", ref=source.name,
                                                ffprobe=FFPROBE, timeout=30)
                project = edl_fixture([measured["source"]], [("s0", 2, 12)])
                before = deepcopy(project)
                target = self.root / f"render-{audio}.mp4"
                report = render.render_edl(project, self.root, target, ffmpeg=FFMPEG,
                                           ffprobe=FFPROBE, timeout=30)
                self.assertEqual(project, before)
                self.assertEqual(report["output"]["total_frames"], 10)
                self.assertEqual(report["output"]["identification_metadata"]["status"], "pass")
                self.assertTrue(report["checks"]["output_identification_metadata_match"])
                self.assertTrue(check_export.inspect_export(target, FFPROBE)["metadata_check_passed"])
                self.assertNotIn("subtitles", report)

    def test_non_media_cli_input_fails_without_disclosing_probe_diagnostics(self):
        target = self.root / "broken.mp4"
        target.write_bytes(b"not media")
        with redirect_stderr(io.StringIO()) as errors:
            result = check_export.main([str(target), "--ffprobe", FFPROBE])
        self.assertEqual(result, 2)
        self.assertNotIn(str(self.root), errors.getvalue())


if __name__ == "__main__":
    unittest.main()
