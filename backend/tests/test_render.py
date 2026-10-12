"""EDL-only cuts, source binding, audio mapping and exclusive verified publication."""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from clipforge.editing import render

RENDERED_BYTES = b"verified MP4 fixture"


def source_fixture(directory, identifier="s0", *, audio=True, fps=None):
    video = directory / f"素材 & {identifier}.mp4"
    video.write_bytes(identifier.encode("ascii"))
    return {"id": identifier, "ref": video.name, "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "total_frames": 300, "width": 160, "height": 90, "fps": fps or {"num": 30, "den": 1},
            "cfr": True, "progressive": True, "sample_aspect_num": 1, "sample_aspect_den": 1,
            "rotation_degrees": 0, "colorspace": 709, "video_stream_index": 2,
            "audio_stream_index": 3 if audio else None}


def edl_fixture(sources, selections=None):
    selections = selections or [(sources[0]["id"], 30, 60), (sources[0]["id"], 0, 1)]
    by_id = {source["id"]: source for source in sources}
    clips, cursor = [], 0
    for index, (identifier, start, end) in enumerate(selections):
        source = by_id[identifier]
        rate = render.frame_rate(source["fps"])
        clips.append({"id": f"clip{index}", "source_id": identifier,
                      "source_in_frame": start, "source_out_frame": end,
                      "timeline_in_frame": cursor, "timeline_out_frame": cursor + end - start,
                      "provenance": {"candidate_id": f"candidate{index}", "candidate_document_sha256": "a" * 64,
                                     "original_source_id": identifier, "original_source_sha256": source["sha256"],
                                     "original_start_seconds": float(Fraction(start, 1) / rate),
                                     "original_end_seconds": float(Fraction(end, 1) / rate),
                                     "original_in_frame": start, "original_out_frame": end,
                                     "original_selection_index": index, "source_sentences": []}})
        cursor += end - start
    return {"schema_version": "0.1.0", "artifact_kind": "edit_decision_list",
            "profile": {key: deepcopy(sources[0][key]) for key in ("width", "height", "fps")},
            "sources": deepcopy(sources), "clips": clips, "duration_frames": cursor}


class RenderHarness:
    def __init__(self, data, root, target):
        self.data, self.root, self.target = data, root, target
        self.commands, self.manifest_calls, self.live = [], [], {}
        self.graph, self.output_changes, self.fact_changes = "", {}, {}
        self.source_changes = {}
        self.failure, self.after_encode, self.after_manifest = None, None, None
        by_id = {source["id"]: source for source in data["sources"]}
        self.has_audio = any(by_id[clip["source_id"]]["audio_stream_index"] is not None for clip in data["clips"])

    @contextmanager
    def open_source(self, path):
        key = str(path)
        with _real_open_source(path) as incoming:
            self.live[key] = self.live.get(key, 0) + 1
            try:
                yield incoming
            finally:
                self.live[key] -= 1

    def manifest(self, path, **options):
        self.manifest_calls.append((Path(path), options))
        if options["source_id"] == "rendered":
            source = deepcopy(self.data["sources"][0])
            source.update(id="rendered", ref=options["ref"], sha256=hashlib.sha256(RENDERED_BYTES).hexdigest(),
                          total_frames=self.data["duration_frames"], video_stream_index=0,
                          audio_stream_index=1 if self.has_audio else None)
            source.update(self.output_changes)
        else:
            source = deepcopy(next(item for item in self.data["sources"] if item["id"] == options["source_id"]))
            source.update(self.source_changes)
        result = {"profile": {key: deepcopy(source[key]) for key in ("width", "height", "fps")}, "source": source}
        if self.after_manifest:
            self.after_manifest()
        return result

    def tool(self, command, timeout):
        self.commands.append((command, timeout))
        if "-filter_complex_script" in command:
            self.graph = Path(command[command.index("-filter_complex_script") + 1]).read_text(encoding="utf-8")
            for source in self.data["sources"]:
                path = str((self.root / source["ref"]).resolve())
                if not self.live.get(path):
                    raise AssertionError("source lock was not held throughout transcoding")
            if self.failure:
                raise self.failure
            Path(command[-1]).write_bytes(RENDERED_BYTES)
            if self.after_encode:
                self.after_encode()
            return ""
        videos = [{"index": 0, "codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}]
        if self.has_audio:
            videos.append({"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                           "channels": 2, "start_pts": 0, "time_base": "1/48000",
                           "duration_ts": render._sample(self.data["duration_frames"], self.data["profile"]["fps"])})
        for stream in videos:
            stream.update(self.fact_changes.get(stream["codec_type"], {}))
        return json.dumps({"streams": videos, "format": {"format_name": "mov,mp4", "tags": {
            "comment": "ClipForge EDL renderer 0.1.0", "editing": "ClipForge"}}})

    def install(self, stack):
        stack.enter_context(patch.object(render, "open_media_source", side_effect=self.open_source))
        stack.enter_context(patch.object(render, "build_media_manifest", side_effect=self.manifest))
        stack.enter_context(patch.object(render, "_probe_text", side_effect=self.tool))


_real_open_source = render.open_media_source


class RenderTests(unittest.TestCase):
    def run_harness(self, data, root, target, *, configure=None, **options):
        harness = RenderHarness(data, root, target)
        if configure:
            configure(harness)
        with ExitStack() as stack:
            harness.install(stack)
            result = render.render_edl(data, root, target, ffmpeg="custom folder/ffmpeg.exe",
                                       ffprobe="custom folder/ffprobe.exe", **options)
        self.assertTrue(all(count == 0 for count in harness.live.values()))
        return result, harness

    def test_saved_edl_range_order_and_same_file_measured_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root)])
            before = deepcopy(data)
            target = root / "result.mp4"
            result, harness = self.run_harness(data, root, target)
            self.assertEqual(data, before)
            self.assertEqual(target.read_bytes(), RENDERED_BYTES)
            self.assertEqual(len(harness.manifest_calls), 2)  # One physical source, then the output.
            self.assertEqual(harness.manifest_calls[0][1]["expected_fps"], data["profile"]["fps"])
            self.assertEqual(result["output"]["total_frames"], 31)
            self.assertEqual(result["output"]["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(result["edl_sha256"], render._json_digest(data))
            self.assertLess(harness.graph.index("trim=start_frame=30:end_frame=60"),
                            harness.graph.index("trim=start_frame=0:end_frame=1"))
            self.assertIn("[v0][v1]concat=n=2:v=1:a=0", harness.graph)
            self.assertIn("[a0][a1]concat=n=2:v=0:a=1", harness.graph)
            command = harness.commands[0][0]
            self.assertEqual(command.count("-i"), 2)
            self.assertIn("[0:2]trim", harness.graph)
            self.assertIn("[1:3]aresample", harness.graph)
            self.assertIn("-copyts", command)
            self.assertIn("-noautorotate", command)
            self.assertEqual(command[command.index("-fps_mode") + 1], "cfr")
            self.assertNotIn("-r", command)
            self.assertNotIn("-ss", command)
            self.assertNotIn("-frames:v", command)
            self.assertNotIn("-shortest", command)
            self.assertIn("editing=ClipForge", command)
            self.assertEqual(command[0], "custom folder/ffmpeg.exe")
            self.assertEqual(harness.commands[1][0][0], "custom folder/ffprobe.exe")
            self.assertTrue(result["checks"]["output_sha256_match"])

    def test_multi_file_mixed_audio_and_silent_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            sources = [source_fixture(root), source_fixture(root, "s1", audio=False)]
            data = edl_fixture(sources, [("s1", 60, 61), ("s0", 30, 60), ("s1", 0, 2)])
            result, harness = self.run_harness(data, root, root / "mixed.mp4")
            self.assertEqual(len(harness.manifest_calls), 3)
            self.assertEqual(result["audio"]["pre_encode_total_samples"], 33 * 1600)
            self.assertEqual([clip["audio_silence"] for clip in result["clips"]], [True, False, True])
            self.assertEqual(harness.graph.count("anullsrc=r=48000:cl=stereo"), 2)
            self.assertIn("aresample=48000:async=1:first_pts=0", harness.graph)
            self.assertLess(harness.graph.index("first_pts=0"), harness.graph.index("atrim=start_sample=48000:end_sample=96000"))
            self.assertEqual(result["output"]["audio_codec"], "aac")
            silent = edl_fixture(sources, [("s1", 2, 3)])
            report, harness = self.run_harness(silent, root, root / "silent.mp4")
            self.assertIn("-an", harness.commands[0][0])
            self.assertNotIn("anullsrc", harness.graph)
            self.assertNotIn("[aout]", harness.graph)
            self.assertEqual(report["audio"]["policy"], "absent")
            self.assertIsNone(report["output"]["audio_stream_index"])

    def test_aliases_of_one_file_reuse_measured_descriptor_and_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            first = source_fixture(root)
            second = deepcopy(first)
            second.update(id="alias", ref=(root / first["ref"]).as_posix())
            data = edl_fixture([first, second], [("alias", 30, 31), ("s0", 0, 1)])
            result, harness = self.run_harness(data, root, root / "aliases.mp4")
            self.assertEqual(len(harness.manifest_calls), 2)
            self.assertEqual(result["sources"][0]["source_ids"], ["s0", "alias"])

    def test_rational_fps_uses_global_sample_rounding_without_cumulative_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root, fps={"num": 30000, "den": 1001})], [("s0", 1, 2), ("s0", 2, 3)])
            result, harness = self.run_harness(data, root, root / "rational.mp4")
            self.assertIn("settb=expr=1001/30000,setpts=N", harness.graph)
            self.assertIn("atrim=start_sample=1602:end_sample=3203", harness.graph)
            self.assertEqual([clip["timeline_audio_samples"] for clip in result["clips"]], [1602, 1601])
            self.assertEqual(result["audio"]["pre_encode_total_samples"], 3203)
            self.assertIn("apad=whole_len=1602,atrim=end_sample=1602", harness.graph)
            self.assertEqual(harness.commands[0][0][harness.commands[0][0].index("-enc_time_base:v") + 1], "1001/30000")

    def test_invalid_edl_odd_profile_and_timeout_rejected_before_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            original = edl_fixture([source_fixture(root)])
            for mutation in (lambda data: data["clips"][0].update(source_out_frame=301),
                             lambda data: data["clips"][0].update(timeline_in_frame=1),
                             lambda data: data["profile"].update(width=161)):
                data = deepcopy(original)
                mutation(data)
                with patch.object(render, "_probe_text") as tool, self.assertRaises(ValueError):
                    render.render_edl(data, root, root / "bad.mp4")
                tool.assert_not_called()
            odd = deepcopy(original)
            odd["sources"][0]["width"] = odd["profile"]["width"] = 161
            with self.assertRaisesRegex(ValueError, "even"):
                render.render_edl(odd, root, root / "odd.mp4")
            for timeout in (0, -1, True, 1.0, 86401):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    render.render_edl(original, root, root / "bad.mp4", timeout=timeout)

    def test_existing_target_and_raced_target_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root)])
            target = root / "existing.mp4"
            target.write_bytes(b"existing")
            with patch.object(render, "build_media_manifest") as probe, self.assertRaises(FileExistsError):
                render.render_edl(data, root, target)
            probe.assert_not_called()
            self.assertEqual(target.read_bytes(), b"existing")
            raced = root / "raced.mp4"
            harness = RenderHarness(data, root, raced)
            harness.after_encode = lambda: raced.write_bytes(b"another creator won")
            with ExitStack() as stack:
                harness.install(stack)
                with self.assertRaises(FileExistsError):
                    render.render_edl(data, root, raced)
            self.assertEqual(raced.read_bytes(), b"another creator won")
            self.assertFalse(list(root.glob(".clipforge-render-*")))
            self.assertTrue(all(count == 0 for count in harness.live.values()))

    def test_wrong_source_hash_and_measured_descriptor_fail_before_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root)])
            (root / data["sources"][0]["ref"]).write_bytes(b"different source content")
            with patch.object(render, "build_media_manifest") as probe, self.assertRaisesRegex(ValueError, "sha256"):
                render.render_edl(data, root, root / "wrong-hash.mp4")
            probe.assert_not_called()
            data = edl_fixture([source_fixture(root)])
            harness = RenderHarness(data, root, root / "wrong-descriptor.mp4")
            harness.source_changes["total_frames"] = 299
            with ExitStack() as stack:
                harness.install(stack)
                with self.assertRaisesRegex(ValueError, "descriptor"):
                    render.render_edl(data, root, harness.target)
            self.assertFalse(harness.commands)
            self.assertTrue(all(count == 0 for count in harness.live.values()))

    def test_tool_failure_and_output_fact_failures_cleanup_temporary_media(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root)])
            failures = [("tool", {"failure": RuntimeError("decode failure")}),
                        ("frames", {"output_changes": {"total_frames": 32}}),
                        ("audio", {"output_changes": {"audio_stream_index": None}}),
                        ("codec", {"fact_changes": {"video": {"pix_fmt": "yuv422p"}}}),
                        ("start", {"fact_changes": {"audio": {"start_pts": 1}}}),
                        ("timebase", {"fact_changes": {"audio": {"time_base": "0/0"}}}),
                        ("duration-type", {"fact_changes": {"audio": {"duration_ts": True}}}),
                        ("duration", {"fact_changes": {"audio": {"duration_ts": 999999}}})]
            for label, changes in failures:
                harness = RenderHarness(data, root, root / f"{label}.mp4")
                for key, value in changes.items():
                    setattr(harness, key, value)
                with self.subTest(label=label), ExitStack() as stack:
                    harness.install(stack)
                    with self.assertRaises((ValueError, RuntimeError)):
                        render.render_edl(data, root, harness.target)
                self.assertFalse(harness.target.exists())
                self.assertFalse(list(root.glob(".clipforge-render-*")))
                self.assertTrue(all(count == 0 for count in harness.live.values()))

    def test_failed_copy_and_wrong_final_hash_remove_only_new_partial_file(self):
        class BrokenReader(io.BytesIO):
            def read(self, count):
                if self.tell():
                    raise OSError("read failed midway")
                return super().read(3)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            target = root / "partial.mp4"
            with self.assertRaisesRegex(OSError, "midway"):
                render._publish(BrokenReader(b"abcdef"), target, "a" * 64, time.monotonic() + 10)
            self.assertFalse(target.exists())
            target = root / "modified.mp4"
            real_open = render.open_media_source
            @contextmanager
            def modify_then_lock(path):
                Path(path).write_bytes(b"modified after copy")
                with real_open(path) as incoming:
                    yield incoming
            with patch.object(render, "open_media_source", side_effect=modify_then_lock):
                with self.assertRaisesRegex(ValueError, "published render sha256"):
                    render._publish(io.BytesIO(RENDERED_BYTES), target,
                                    hashlib.sha256(RENDERED_BYTES).hexdigest(), time.monotonic() + 10)
            self.assertFalse(target.exists())

    def test_shared_deadline_decreases_manifest_and_transcode_budgets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root), source_fixture(root, "s1")], [("s1", 0, 1), ("s0", 1, 2)])
            harness = RenderHarness(data, root, root / "budget.mp4")
            clock = [100.0]
            harness.after_manifest = lambda: clock.__setitem__(0, clock[0] + 5)
            harness.after_encode = lambda: clock.__setitem__(0, clock[0] + 5)
            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render.time, "monotonic", side_effect=lambda: clock[0]))
                render.render_edl(data, root, harness.target, timeout=60)
            budgets = [options["timeout"] for _, options in harness.manifest_calls]
            self.assertEqual(budgets, [59, 54, 44])
            self.assertLess(harness.commands[1][1], harness.commands[0][1])
            harness = RenderHarness(data, root, root / "exhausted.mp4")
            clock[0] = 100
            harness.after_manifest = lambda: clock.__setitem__(0, clock[0] + 2)
            with ExitStack() as stack:
                harness.install(stack)
                stack.enter_context(patch.object(render.time, "monotonic", side_effect=lambda: clock[0]))
                with self.assertRaisesRegex(RuntimeError, "remaining verification budget"):
                    render.render_edl(data, root, harness.target, timeout=3)
            self.assertFalse(harness.target.exists())
            self.assertFalse(harness.commands)

    @unittest.skipUnless(os.name == "nt", "Windows source sharing contract")
    def test_actual_source_write_denied_during_encode_and_released_afterward(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root)])
            source = root / data["sources"][0]["ref"]
            def attempt_write():
                with self.assertRaises(OSError):
                    source.write_bytes(b"replacement")
            self.run_harness(data, root, root / "locked.mp4", configure=lambda harness: setattr(harness, "after_encode", attempt_write))
            source.write_bytes(b"released")
            self.assertEqual(source.read_bytes(), b"released")

    def test_cli_existing_output_fails_without_media_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = edl_fixture([source_fixture(root)])
            saved, target = root / "edit.json", root / "existing.mp4"
            saved.write_text(json.dumps(data), encoding="utf-8")
            target.write_bytes(b"keep existing output")
            result = subprocess.run([sys.executable, "-m", "clipforge.editing.render", str(saved),
                                     "--source-root", str(root), "--output", str(target)],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr)
            self.assertEqual(target.read_bytes(), b"keep existing output")


if __name__ == "__main__":
    unittest.main()
