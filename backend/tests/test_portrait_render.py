"""Saved native center crops render once into a measured portrait profile."""

from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clipforge.editing import edl, render
from clipforge.editing.subtitles import build_subtitles, to_ass
from backend.tests.test_render import RenderHarness, edl_fixture, source_fixture
from backend.tests.test_subtitle_render import font_fixture, good_log

ROOT = Path(__file__).resolve().parents[2]


def native_source(root, identifier="s0", *, width=1920, height=1080, audio=True):
    source = source_fixture(root, identifier, audio=audio)
    source.update(width=width, height=height)
    return source


class PortraitHarness(RenderHarness):
    def __init__(self, data, root, target):
        super().__init__(data, root, target)
        self.output_changes.update(width=data["profile"]["width"], height=data["profile"]["height"])


class PortraitRenderTests(unittest.TestCase):
    def run_harness(self, data, root, target, *, configure=None, **options):
        harness = PortraitHarness(data, root, target)
        if configure is not None:
            configure(harness)
        with ExitStack() as stack:
            harness.install(stack)
            report = render.render_edl(data, root, target, ffmpeg="custom/ffmpeg",
                                       ffprobe="custom/ffprobe", **options)
        self.assertTrue(all(count == 0 for count in harness.live.values()))
        return report, harness

    def test_exact_native_crop_precedes_output_scale_without_changing_cuts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original = edl_fixture([native_source(root)])
            portrait = edl.create_portrait_edl(original)
            before = deepcopy(portrait)
            report, harness = self.run_harness(portrait, root, root / "portrait.mp4")
            self.assertEqual(portrait, before)
            self.assertEqual(portrait["sources"], original["sources"])
            self.assertEqual(portrait["clips"], original["clips"])
            self.assertEqual(report["output"]["profile"], {"width": 1080, "height": 1920,
                                                          "fps": {"num": 30, "den": 1}})
            self.assertEqual(report["output"]["total_frames"], original["duration_frames"])
            self.assertEqual(report["framing"], {
                "mode": "center_crop",
                "sources": [{"source_id": "s0", "x": 662, "y": 12, "width": 594, "height": 1056}]})
            self.assertEqual(report["edl_sha256"], render._json_digest(portrait))
            self.assertIn("trim=start_frame=30:end_frame=60", harness.graph)
            self.assertIn("setsar=1,crop=w=594:h=1056:x=662:y=12,scale=w=1080:h=1920:", harness.graph)
            self.assertLess(harness.graph.index("trim=start_frame=30"), harness.graph.index("crop=w=594"))
            self.assertNotIn("scale=w=iw:h=ih", harness.graph)
            self.assertEqual(report["audio"]["pre_encode_total_samples"], 31 * 1600)

    def test_mixed_native_dimensions_and_clip_order_use_source_id_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sources = [native_source(root), native_source(root, "s1", width=1280, height=720, audio=False)]
            # v0.2 permits different native dimensions with one output profile.
            portrait = edl_fixture(sources, [("s1", 60, 61), ("s0", 30, 60), ("s1", 0, 2)])
            portrait["schema_version"] = "0.2.0"
            portrait["profile"] = {"width": 1080, "height": 1920, "fps": {"num": 30, "den": 1}}
            # Assemble a v0.2 fixture from each source's formally derived crop.
            # A mixed-native-size project cannot first be a valid v0.1 EDL.
            portrait["framing"] = {"mode": "center_crop", "sources": [
                edl.create_portrait_edl(edl_fixture([source]))["framing"]["sources"][0] for source in sources]}
            edl.validate_edl(portrait)
            report, harness = self.run_harness(portrait, root, root / "mixed.mp4")
            self.assertEqual([entry["source_id"] for entry in report["clips"]], ["s1", "s0", "s1"])
            self.assertEqual([entry["audio_silence"] for entry in report["clips"]], [True, False, True])
            self.assertIn("[0:2]trim=start_frame=60:end_frame=61", harness.graph)
            self.assertEqual(harness.graph.count("crop=w=396:h=704:x=442:y=8"), 2)
            self.assertEqual(harness.graph.count("crop=w=594:h=1056:x=662:y=12"), 1)
            self.assertEqual(harness.graph.count("scale=w=1080:h=1920:"), 3)
            self.assertEqual(len(harness.manifest_calls), 3)
            self.assertEqual(report["output"]["total_frames"], 33)
            self.assertIn("[v0][v1][v2]concat=n=3:v=1:a=0", harness.graph)

    def test_native_portrait_source_uses_full_frame_crop_and_stays_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portrait = edl.create_portrait_edl(edl_fixture(
                [native_source(root, width=1080, height=1920, audio=False)]))
            report, harness = self.run_harness(portrait, root, root / "silent.mp4")
            self.assertIn("crop=w=1080:h=1920:x=0:y=0,scale=w=1080:h=1920:", harness.graph)
            self.assertEqual(report["audio"]["policy"], "absent")
            self.assertIn("-an", harness.commands[0][0])

    def test_measured_native_profile_and_output_profile_are_checked_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portrait = edl.create_portrait_edl(edl_fixture([native_source(root)]))
            target = root / "wrong-native.mp4"
            harness = PortraitHarness(portrait, root, target)
            native_manifest = harness.manifest

            def wrong_native(path, **options):
                result = native_manifest(path, **options)
                if options["source_id"] != "rendered":
                    result["profile"] = deepcopy(portrait["profile"])
                return result

            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render, "build_media_manifest", side_effect=wrong_native))
                with self.assertRaisesRegex(ValueError, "source descriptor"):
                    render.render_edl(portrait, root, target)
            self.assertFalse(harness.commands)
            self.assertFalse(target.exists())
            for changes in ({"width": 1920, "height": 1080}, {"total_frames": 32}):
                target = root / ("wrong-" + next(iter(changes)) + ".mp4")
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "frame count/profile"):
                    self.run_harness(portrait, root, target,
                                     configure=lambda item: item.output_changes.update(changes))
                self.assertFalse(target.exists())

    def test_invalid_saved_portrait_contract_fails_before_any_media_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            valid = edl.create_portrait_edl(edl_fixture([native_source(root)]))
            mutations = [
                lambda data: data["framing"]["sources"][0].update(x=664),
                lambda data: data["framing"]["sources"][0].update(width=True),
                lambda data: data["framing"]["sources"].clear(),
                lambda data: data["framing"]["sources"].append(deepcopy(data["framing"]["sources"][0])),
                lambda data: data["profile"].update(width=1920),
                lambda data: data["sources"][0].update(fps={"num": 25, "den": 1}),
            ]
            for mutate in mutations:
                changed = deepcopy(valid)
                mutate(changed)
                with self.subTest(changed=changed), patch.object(render, "_probe_text") as tool:
                    with patch.object(render, "build_media_manifest") as measure:
                        with self.assertRaises(ValueError):
                            render.render_edl(changed, root, root / "invalid.mp4")
                        tool.assert_not_called()
                        measure.assert_not_called()

    def test_ass_burns_after_crop_scale_concat_using_portrait_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portrait = edl.create_portrait_edl(edl_fixture([native_source(root)]))
            analysis = json.loads((ROOT / "docs/samples/analysis.example.json").read_text(encoding="utf-8"))
            analysis["media"]["normalized_sha256"] = portrait["sources"][0]["sha256"]
            analyses = {"s0": analysis}
            plan = build_subtitles(portrait, analyses)
            font = root / "font.ttf"
            font.write_bytes(font_fixture())
            target = root / "subtitled.mp4"
            harness = PortraitHarness(portrait, root, target)
            folders = []

            def subtitle_tool(command, timeout, *, cwd=None):
                if "-filters" in command:
                    return " ... ass V->V Render ASS subtitles\n", ""
                folders.append(Path(cwd))
                ass = Path(cwd, "subtitles.ass").read_text(encoding="utf-8")
                self.assertEqual(ass, to_ass(plan, font_name="TestFont"))
                self.assertIn("PlayResX: 1080\nPlayResY: 1920", ass)
                simulated = command.copy()
                simulated[simulated.index("-filter_complex_script") + 1] = str(Path(cwd, "filters.txt"))
                simulated[-1] = str(Path(cwd, "rendered.mp4"))
                harness.tool(simulated, timeout)
                return "", good_log()

            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render, "_subtitle_tool", side_effect=subtitle_tool))
                report = render.render_edl(portrait, root, target, subtitle_plan=plan, analyses=analyses,
                                           font_file=font, font_name="TestFont")
            self.assertLess(harness.graph.index("crop=w=594"), harness.graph.index("concat=n=2:v=1:a=0"))
            self.assertLess(harness.graph.index("concat=n=2:v=1:a=0"), harness.graph.index("[vout]ass="))
            self.assertTrue(report["checks"]["subtitles_burned"])
            self.assertEqual(report["subtitles"]["plan_sha256"], render._json_digest(plan))
            self.assertEqual(report["framing"], portrait["framing"])
            self.assertEqual(len(folders), 1)
            self.assertFalse(folders[0].exists())

    def test_version_01_keeps_native_graph_and_report_without_framing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data = edl_fixture([source_fixture(root)])
            graph, _, _ = render._filter_graph(data)
            expected = ("[0:2]trim=start_frame=30:end_frame=60,settb=expr=1/30,setpts=N,setsar=1,"
                        "scale=w=iw:h=ih:in_range=auto:out_range=tv:in_color_matrix=bt709:"
                        "out_color_matrix=bt709,format=yuv420p[v0]")
            self.assertEqual(graph.split(";\n")[0], expected)
            report, _ = self.run_harness(data, root, root / "legacy.mp4")
            self.assertNotIn("framing", report)
            self.assertEqual(report["output"]["profile"], data["profile"])


if __name__ == "__main__":
    unittest.main()
