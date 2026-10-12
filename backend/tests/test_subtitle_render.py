"""Subtitle burn-in failure boundaries, font binding and unchanged EDL cuts."""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from clipforge.editing import render
from clipforge.editing.subtitles import build_subtitles, to_ass
from backend.tests.test_render import RenderHarness, edl_fixture, source_fixture

ROOT = Path(__file__).resolve().parents[2]
CHARS = "AI辅助剪辑大家好。开始学习字幕测试ABC"


def font_fixture(characters=CHARS):
    """A tiny OpenType name/cmap fixture; native FreeType is tested separately."""
    strings, records = bytearray(), []
    for identifier, value in ((1, "TestFont"), (6, "TestFont-Regular"), (16, "TestFont")):
        encoded = value.encode("utf-16-be")
        records.append(struct.pack(">6H", 3, 1, 0x409, identifier, len(encoded), len(strings)))
        strings.extend(encoded)
    name = struct.pack(">3H", 0, len(records), 6 + 12 * len(records)) + b"".join(records) + strings
    points = sorted({ord(char) for char in characters})
    groups = b"".join(struct.pack(">III", point, point, index + 1) for index, point in enumerate(points))
    mapping = struct.pack(">HHIII", 12, 0, 16 + len(groups), 0, len(points)) + groups
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + mapping
    tables = [(b"name", bytes(name)), (b"cmap", cmap),
              (b"maxp", struct.pack(">IH", 0x10000, len(points) + 1))]
    position, directory, body = 12 + len(tables) * 16, bytearray(), bytearray()
    for tag, value in tables:
        directory.extend(struct.pack(">4sIII", tag, 0, position, len(value)))
        body.extend(value)
        position += len(value)
    return struct.pack(">IHHHH", 0x10000, len(tables), 0, 0, 0) + directory + body


def inputs(root):
    source = source_fixture(root)
    edl = edl_fixture([source])
    analysis = json.loads((ROOT / "docs/samples/analysis.example.json").read_text(encoding="utf-8"))
    analysis["media"]["normalized_sha256"] = source["sha256"]
    analyses = {source["id"]: analysis}
    return edl, analyses, build_subtitles(edl, analyses)


def good_log():
    return ("[verbose] libass API version: test\n"
            "[verbose] Loading font file 'fonts/font.ttf'\n"
            "[info] fontselect: (TestFont, 400, 0) -> TestFont-Regular, 0, TestFont-Regular\n")


class SubtitleRenderTests(unittest.TestCase):
    def test_unicode_font_family_and_missing_glyph_fail_closed(self):
        info = render._font_info(font_fixture(), "TestFont", {ord(char) for char in CHARS})
        self.assertEqual(info["face_indices"], [0])
        self.assertEqual(info["postscript_names"], ["TestFont-Regular"])
        with self.assertRaisesRegex(ValueError, "family"):
            render._font_info(font_fixture(), "OtherFont", {ord("A")})
        with self.assertRaisesRegex(ValueError, "U\\+10FFFF"):
            render._font_info(font_fixture(), "TestFont", {0x10FFFF})
        with self.assertRaises(ValueError):
            render._font_info(font_fixture()[:-5], "TestFont", {ord("A")})

    def test_bmp_cmap_delta_and_range_offset(self):
        # Two format-4 segments: A -> glyph 1, followed by the required sentinel.
        mapping = struct.pack(">7H", 4, 32, 0, 4, 4, 1, 0)
        mapping += struct.pack(">2HH2H2H2H", 65, 65535, 0, 65, 65535, 65472, 1, 0, 0)
        table = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + mapping
        lookup = render._font_cmap(table, 3)
        self.assertEqual(lookup(65), 1)
        self.assertEqual(lookup(66), 0)
        self.assertEqual(lookup(0x10000), 0)
        # Nonzero idRangeOffset addresses the glyph array relative to its own
        # word; the idDelta is then applied only to a nonzero mapped glyph.
        mapping = struct.pack(">7H", 4, 34, 0, 4, 4, 1, 0)
        mapping += struct.pack(">2HH2H2H2HH", 65, 65535, 0, 65, 65535, 1, 1, 4, 0, 1)
        lookup = render._font_cmap(struct.pack(">HHHHI", 0, 1, 3, 1, 12) + mapping, 3)
        self.assertEqual(lookup(65), 2)
        self.assertEqual(lookup(66), 0)

    def test_actual_libass_logs_reject_fallback_and_missing_glyph(self):
        state = {"filename": "font.ttf", "report": {"postscript_names": ["TestFont-Regular"], "face_indices": [0]}}
        render._validate_subtitle_logs(good_log(), state)
        for diagnostic in (good_log() + "Glyph 0x4E00 not found, selecting one more font\n",
                           good_log().replace("-> TestFont-Regular", "-> OtherFont").replace(", 0, TestFont-Regular", ", 0, OtherFont"),
                           good_log().replace(", 0, TestFont-Regular", ", 1, TestFont-Regular"),
                           good_log() + "[warning] ass_read_file(fonts/font.ttf): fopen failed\n",
                           good_log() + "[error] decoding failure\n",
                           "libass without a confirmed font load"):
            with self.subTest(diagnostic=diagnostic), self.assertRaises(RuntimeError):
                render._validate_subtitle_logs(diagnostic, state)

    def test_render_burns_saved_plan_after_concat_and_reports_exact_inputs(self):
        with tempfile.TemporaryDirectory(prefix="subtitle [,]' ") as directory:
            root = Path(directory).resolve()
            edl, analyses, plan = inputs(root)
            original_edl, original_plan = deepcopy(edl), deepcopy(plan)
            font = root / "font [,]' .ttf"
            font.write_bytes(font_fixture())
            target = root / "output [,]' .mp4"
            harness = RenderHarness(edl, root, target)
            calls = []

            def subtitle_tool(command, timeout, *, cwd=None):
                calls.append((command, timeout, cwd))
                if "-filters" in command:
                    return " ... ass V->V Render ASS subtitles\n", ""
                self.assertIsNotNone(cwd)
                self.assertFalse(Path(cwd).is_relative_to(target.parent))
                self.assertEqual(Path(cwd, "subtitles.ass").read_text(encoding="utf-8"),
                                 to_ass(plan, font_name="TestFont"))
                self.assertEqual(Path(cwd, "fonts/font.ttf").read_bytes(), font.read_bytes())
                self.assertEqual(command[command.index("-map") + 1], "[subout]")
                self.assertEqual(command[command.index("-filter_complex_script") + 1], "filters.txt")
                self.assertEqual(command[-1], "rendered.mp4")
                if os.name == "nt":
                    for held in (font, Path(cwd, "subtitles.ass"), Path(cwd, "fonts/font.ttf")):
                        with self.assertRaises(OSError):
                            with held.open("r+b"):
                                pass
                simulated = command.copy()
                simulated[simulated.index("-filter_complex_script") + 1] = str(Path(cwd, "filters.txt"))
                simulated[-1] = str(Path(cwd, "rendered.mp4"))
                harness.tool(simulated, timeout)
                return "", good_log()

            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render, "_subtitle_tool", side_effect=subtitle_tool))
                stack.enter_context(patch.dict(os.environ, {"SUBTITLE_FONT_FILE": "wrong", "SUBTITLE_FONT_NAME": "OtherFont"}))
                report = render.render_edl(edl, root, target, subtitle_plan=plan, analyses=analyses,
                                           font_file=font, font_name="TestFont", ffmpeg="custom/ffmpeg",
                                           ffprobe="custom/ffprobe")
            self.assertEqual(edl, original_edl)
            self.assertEqual(plan, original_plan)
            self.assertIn(";\n[vout]ass=filename=subtitles.ass:fontsdir=fonts[subout]", harness.graph)
            self.assertEqual(report["output"]["total_frames"], edl["duration_frames"])
            subtitle_report = report["subtitles"]
            self.assertEqual(subtitle_report["plan_sha256"], render._json_digest(plan))
            self.assertEqual(subtitle_report["font_sha256"], hashlib.sha256(font.read_bytes()).hexdigest())
            self.assertEqual(subtitle_report["ass_sha256"], hashlib.sha256(to_ass(plan, font_name="TestFont").encode()).hexdigest())
            self.assertEqual(subtitle_report["font_name"], "TestFont")
            self.assertTrue(report["checks"]["subtitles_burned"])
            self.assertEqual(len(calls), 2)
            self.assertFalse(any(root.glob(".clipforge-render-*")))
            self.assertFalse(Path(calls[-1][2]).exists())
            self.assertTrue(all(value == 0 for value in harness.live.values()))

    def test_invalid_plan_font_and_libass_never_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            edl, analyses, plan = inputs(root)
            font = root / "font.ttf"
            font.write_bytes(font_fixture())
            target = root / "output.mp4"
            changed = deepcopy(plan)
            changed["cues"][0]["text"] = "修改"
            with patch.object(render, "_subtitle_tool") as tool:
                with self.assertRaises(ValueError):
                    render.render_edl(edl, root, target, subtitle_plan=changed, analyses=analyses,
                                      font_file=font, font_name="TestFont")
                tool.assert_not_called()
            for family in ("TestFont,Other", r"Test\Font", "Test{Font}", " TestFont", "", None):
                with patch.dict(os.environ, {}, clear=True):
                    with self.subTest(family=family), self.assertRaises(ValueError):
                        render.render_edl(edl, root, target, subtitle_plan=plan, analyses=analyses,
                                          font_file=font, font_name=family)
            with patch.object(render, "_subtitle_tool", return_value=(" ... scale V->V Scale\n", "")):
                with self.assertRaisesRegex(RuntimeError, "libass"):
                    render.render_edl(edl, root, target, subtitle_plan=plan, analyses=analyses,
                                      font_file=font, font_name="TestFont")
            self.assertFalse(target.exists())

    def test_environment_font_configuration_and_no_missing_character_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            edl, analyses, plan = inputs(root)
            font = root / "font.ttf"
            font.write_bytes(font_fixture().replace("TestFont".encode("utf-16-be"), "BadFont!".encode("utf-16-be")))
            with patch.dict(os.environ, {"SUBTITLE_FONT_FILE": str(font), "SUBTITLE_FONT_NAME": "TestFont"}):
                with self.assertRaisesRegex(ValueError, "family"):
                    render.render_edl(edl, root, root / "output.mp4", subtitle_plan=plan, analyses=analyses)
            font.write_bytes(font_fixture("AI"))
            with patch.object(render, "_subtitle_tool") as tool:
                with self.assertRaisesRegex(ValueError, "glyph"):
                    render.render_edl(edl, root, root / "output.mp4", subtitle_plan=plan, analyses=analyses,
                                      font_file=font, font_name="TestFont")
                tool.assert_not_called()

    def test_render_diagnostic_failure_removes_temp_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            edl, analyses, plan = inputs(root)
            font = root / "font.ttf"
            font.write_bytes(font_fixture())
            target = root / "output.mp4"
            harness = RenderHarness(edl, root, target)

            def tool(command, timeout, *, cwd=None):
                if "-filters" in command:
                    return " ... ass V->V Render ASS subtitles\n", ""
                simulated = command.copy()
                simulated[simulated.index("-filter_complex_script") + 1] = str(Path(cwd, "filters.txt"))
                simulated[-1] = str(Path(cwd, "rendered.mp4"))
                harness.tool(simulated, timeout)
                return "", good_log() + "Glyph 0x4E00 not found\n"

            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render, "_subtitle_tool", side_effect=tool))
                with self.assertRaisesRegex(RuntimeError, "missing glyph"):
                    render.render_edl(edl, root, target, subtitle_plan=plan, analyses=analyses,
                                      font_file=font, font_name="TestFont")
            self.assertFalse(target.exists())
            self.assertFalse(any(root.glob(".clipforge-render-*")))
            self.assertTrue(all(value == 0 for value in harness.live.values()))

    def test_diagnostic_runner_times_out_after_parent_exits_with_inherited_pipes(self):
        wrapper = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time;time.sleep(.6)'],"
                   "stdout=sys.stdout,stderr=sys.stderr);print('diagnostic',flush=True)")
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            render._subtitle_tool([sys.executable, "-c", wrapper], .15)
        self.assertLess(time.monotonic() - started, .5)

    def test_tool_failure_logs_only_bounded_internal_tail_and_returns_safe_error(self):
        code = "import sys;sys.stderr.write('private-head:'+'x'*9000+'diagnostic-tail');sys.exit(7)"
        with self.assertLogs(render.logger, level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "^FFmpeg subtitle rendering failed$"):
                render._subtitle_tool([sys.executable, "-c", code], 5)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("status 7", logs.output[0])
        self.assertIn("diagnostic-tail", logs.output[0])
        self.assertNotIn("private-head", logs.output[0])
        self.assertLess(len(logs.output[0]), 8500)


if __name__ == "__main__":
    unittest.main()
