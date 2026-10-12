"""Measured stream/PTS contracts, source integrity and real watchdog behavior."""

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from clipforge.editing import manifest


def raw_fixture(audio=True, index=0):
    streams = [{"index": index, "codec_type": "video", "codec_name": "h264",
                "width": 960, "height": 600, "avg_frame_rate": "30/1", "r_frame_rate": "30/1",
                "time_base": "1/15360", "field_order": "progressive", "sample_aspect_ratio": "1:1",
                "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709",
                "disposition": {"attached_pic": 0}, "tags": {}}]
    if audio:
        streams.append({"index": index + 1, "codec_type": "audio", "codec_name": "aac",
                        "sample_rate": "48000", "channels": 2})
    return {"streams": streams, "format": {"duration": "0.1", "format_name": "mp4"}}


def frame_line(pts, index=0, **changes):
    fields = {"stream_index": str(index), "pts": str(pts), "width": "960", "height": "600",
              "sample_aspect_ratio": "1:1", "interlaced_frame": "0", "color_space": "bt709",
              "color_primaries": "bt709", "color_transfer": "bt709"}
    fields.update(changes)
    return "frame|" + "|".join(f"{key}={value}" for key, value in fields.items() if value is not None) + "\n"


class ManifestTests(unittest.TestCase):
    def run_fixture(self, raw=None, lines=None, *, count=3, **options):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            video = Path(directory) / "视频 & example.mp4"
            video.write_bytes(b"local media fixture")
            raw = raw_fixture() if raw is None else raw
            lines = [frame_line(pts) for pts in (0, 512, 1024)] if lines is None else lines
            details = stack.enter_context(patch.object(manifest, "_probe_details", return_value=raw))
            frames = stack.enter_context(patch.object(manifest, "_probe_lines", side_effect=lambda *a: (line for line in lines)))
            counter = stack.enter_context(patch.object(manifest, "read_frame_count", return_value=count))
            result = manifest.build_media_manifest(video, **options)
            return result, details.call_args, frames.call_args, counter.call_args

    def test_measured_source_contract_hash_and_explicit_tool(self):
        result, details, frames, counter = self.run_fixture(source_id="normalized-test", ref="normalized.mp4",
                                                          ffprobe="custom folder/ffprobe.exe")
        self.assertEqual(set(result), {"profile", "source"})
        source = result["source"]
        self.assertEqual(result["profile"], {"width": 960, "height": 600, "fps": {"num": 30, "den": 1}})
        self.assertEqual(source["sha256"], hashlib.sha256(b"local media fixture").hexdigest())
        self.assertEqual(source["total_frames"], 3)
        self.assertEqual(source["id"], "normalized-test")
        self.assertEqual(source["ref"], "normalized.mp4")
        self.assertEqual(source["audio_stream_index"], 1)
        self.assertTrue(source["cfr"] and source["progressive"])
        self.assertEqual(details.args[1], "custom folder/ffprobe.exe")
        self.assertEqual(frames.args[0][0], "custom folder/ffprobe.exe")
        self.assertEqual(counter.args[1:3], (0, "custom folder/ffprobe.exe"))
        self.assertTrue(counter.kwargs["count_frames"])
        self.assertIs(counter.kwargs["runner"], manifest._probe_text)

    def test_no_audio_nonzero_video_index_and_attached_picture(self):
        raw = raw_fixture(audio=False, index=3)
        raw["streams"].insert(0, {"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}})
        result, _, frames, counter = self.run_fixture(raw, [frame_line(pts, index=3) for pts in (0, 512, 1024)])
        self.assertIsNone(result["source"]["audio_stream_index"])
        self.assertEqual(result["source"]["video_stream_index"], 3)
        self.assertEqual(counter.args[1], 3)
        self.assertEqual(frames.args[0][frames.args[0].index("-select_streams") + 1], "3")
        self.assertTrue(Path(result["source"]["ref"]).is_absolute())

    def test_fractional_timebase_tick_quantization_is_bounded(self):
        raw = raw_fixture()
        raw["streams"][0]["time_base"] = "1/1000"
        result, _, _, _ = self.run_fixture(raw, [frame_line(pts) for pts in (0, 33, 67, 100)], count=4)
        self.assertEqual(result["source"]["total_frames"], 4)
        with self.assertRaisesRegex(ValueError, "timestamps"):
            self.run_fixture(raw, [frame_line(pts) for pts in (0, 33, 70)])

    def test_false_average_30_missing_repeated_and_noncontiguous_pts(self):
        cases = ([0, 512, 1536], [0, 512, 512], [0, 513, 1024], [0, 512, 1000], [512, 1024, 1536])
        for positions in cases:
            with self.subTest(positions=positions), self.assertRaises(ValueError):
                self.run_fixture(lines=[frame_line(pts) for pts in positions])
        for value in ("N/A", None, "0.033", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_fixture(lines=[frame_line(0), frame_line(value)])
        with self.assertRaisesRegex(ValueError, "no decoded"):
            self.run_fixture(lines=[])
        with self.assertRaisesRegex(ValueError, "count differs"):
            self.run_fixture(count=2)

    def test_stream_unknowns_and_unsupported_profile_rejected(self):
        changes = [{"field_order": "unknown"}, {"field_order": "tt"}, {"sample_aspect_ratio": "N/A"},
                   {"sample_aspect_ratio": "4:3"}, {"color_space": "unknown"},
                   {"color_transfer": None}, {"color_primaries": "bt2020"}, {"width": 0},
                   {"width": True}, {"avg_frame_rate": "30000/1001"}, {"r_frame_rate": "25/1"},
                   {"time_base": "0/0"}, {"time_base": "1/20"}, {"tags": {"rotate": "90"}},
                   {"tags": {"rotate": "N/A"}}, {"side_data_list": [{"rotation": -90}]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                raw = raw_fixture()
                raw["streams"][0].update(change)
                self.run_fixture(raw)
        raw = raw_fixture()
        raw["streams"][0]["disposition"]["attached_pic"] = 1
        with self.assertRaisesRegex(ValueError, "封面"):
            self.run_fixture(raw)

    def test_each_decoded_frame_matches_properties(self):
        for change in ({"interlaced_frame": "1"}, {"interlaced_frame": None}, {"width": "640"},
                       {"height": "480"}, {"sample_aspect_ratio": "2:1"},
                       {"color_space": "bt601"}, {"color_transfer": None}, {"color_primaries": "unknown"},
                       {"stream_index": "9"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_fixture(lines=[frame_line(0), frame_line(512, **change), frame_line(1024)])
        with self.assertRaisesRegex(ValueError, "unexpected frame"):
            self.run_fixture(lines=["packet|pts=0\n"])
        with self.assertRaisesRegex(ValueError, "duplicate frame"):
            self.run_fixture(lines=[frame_line(0).rstrip() + "|pts=0\n"])

    def test_decoded_sei_rotation_with_prefixed_and_legacy_compact_fields(self):
        for prefix in ("", "side_datum/3x3_displaymatrix:"):
            for rotation in ("90", "-90", "N/A", None):
                changes = {prefix + "side_data_type": "3x3 displaymatrix", prefix + "rotation": rotation}
                with self.subTest(prefix=prefix, rotation=rotation), self.assertRaisesRegex(ValueError, "rotation|rotated"):
                    self.run_fixture(lines=[frame_line(0, **changes), frame_line(512), frame_line(1024)])
            result, _, _, _ = self.run_fixture(lines=[frame_line(0, **{prefix + "side_data_type": "3x3 displaymatrix",
                                                                     prefix + "rotation": "0"}),
                                                       frame_line(512), frame_line(1024)])
            self.assertEqual(result["source"]["rotation_degrees"], 0)

    def test_optional_expected_fps_is_explicit_and_strict(self):
        raw = raw_fixture()
        raw["streams"][0].update(avg_frame_rate="30000/1001", r_frame_rate="30000/1001", time_base="1/30000")
        result, _, _, _ = self.run_fixture(raw, [frame_line(pts) for pts in (0, 1001, 2002)],
                                          expected_fps={"num": 30000, "den": 1001})
        self.assertEqual(result["profile"]["fps"], {"num": 30000, "den": 1001})
        for fps in ({"num": 30.0, "den": 1}, {"num": True, "den": 1}, {"num": 60, "den": 2}, "30/1"):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                self.run_fixture(expected_fps=fps)
        for timeout in (True, 1.0, 0, -1, 86401):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.run_fixture(timeout=timeout)

    @unittest.skipIf(os.name == "nt", "Windows rejects the attempted write through its shared read handle")
    def test_file_changed_during_probe_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            video.write_bytes(b"original")
            def replace_file(*args, **kwargs):
                video.write_bytes(b"changed contents")
                return 3
            with patch.object(manifest, "_probe_details", return_value=raw_fixture()), \
                    patch.object(manifest, "_probe_lines", side_effect=lambda *a: (frame_line(p) for p in (0, 512, 1024))), \
                    patch.object(manifest, "read_frame_count", side_effect=replace_file):
                with self.assertRaisesRegex(ValueError, "file changed"):
                    manifest.build_media_manifest(video)

    @unittest.skipUnless(os.name == "nt", "Windows FILE_SHARE_READ contract")
    def test_windows_verification_handle_denies_writes_and_replacement_then_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            replacement = Path(directory) / "replacement.mp4"
            video.write_bytes(b"original")
            replacement.write_bytes(b"replacement")
            with manifest.open_media_source(video) as incoming:
                with self.assertRaises(OSError):
                    video.write_bytes(b"changed")
                with self.assertRaises(OSError):
                    os.replace(replacement, video)
                self.assertEqual(incoming.read(), b"original")
            video.write_bytes(b"released")
            self.assertEqual(video.read_bytes(), b"released")

    def test_local_regular_file_and_reference_required(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "ordinary"):
                manifest.build_media_manifest(Path(directory))
        for reference in ("https://example.invalid/video.mp4", "../escape.mp4", "\\\\server\\video.mp4"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.run_fixture(ref=reference)

    def test_real_streaming_process_exit_stderr_timeout_and_line_limit(self):
        lines = list(manifest._probe_lines([sys.executable, "-c", "print('frame|pts=0')"], 3))
        self.assertEqual([line.strip() for line in lines], ["frame|pts=0"])
        for code in ("import sys;sys.exit(2)", "import sys;print('decode error', file=sys.stderr)"):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, "verification failed"):
                list(manifest._probe_lines([sys.executable, "-c", code], 3))
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            list(manifest._probe_lines([sys.executable, "-c", "import time;time.sleep(10)"], 0.2))
        self.assertLess(time.monotonic() - started, 0.2 + manifest.CLEANUP_TIMEOUT + 0.3)
        # A wrapper can exit while its descendant still owns stdout/stderr.
        # The caller's deadline must hold despite inherited open pipes.
        wrapper = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.8)'])"
        started = time.monotonic()
        original_popen, processes = subprocess.Popen, []
        def record_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(manifest.subprocess, "Popen", side_effect=record_process):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                list(manifest._probe_lines([sys.executable, "-c", wrapper], 0.15))
        self.assertLess(time.monotonic() - started, 0.7)
        self.assertTrue(processes[0].stdout.closed and processes[0].stderr.closed)
        with self.assertRaisesRegex(ValueError, "line exceeds"):
            list(manifest._probe_lines([sys.executable, "-c", f"print('x'*{manifest.MAX_LINE_BYTES + 1})"], 3))

    def test_bounded_header_parser(self):
        with patch.object(manifest, "_probe_lines", side_effect=lambda *a: (line for line in ['{"streams":[]}\n'])), \
                patch.object(manifest, "MAX_METADATA_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "metadata exceeds"):
                manifest._probe_details(Path("unused.mp4"), "unused", 1)

    def test_frame_count_reuses_bounded_runner_and_error_check(self):
        original = manifest._probe_lines
        command = [sys.executable, "-c", 'print(\'{"streams":[{"nb_read_frames":"3"}]}\')']
        with patch.object(manifest, "_probe_lines", side_effect=lambda cmd, timeout: original(command, timeout)) as runner:
            count = manifest.read_frame_count(Path("unused.mp4"), 3, "ffprobe", 3,
                                              count_frames=True, runner=manifest._probe_text)
        self.assertEqual(count, 3)
        self.assertIn("-count_frames", runner.call_args.args[0])
        command = [sys.executable, "-c", "import sys;print('decode error',file=sys.stderr)"]
        with patch.object(manifest, "_probe_lines", side_effect=lambda cmd, timeout: original(command, timeout)):
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                manifest.read_frame_count(Path("unused.mp4"), 3, "ffprobe", 3,
                                           count_frames=True, runner=manifest._probe_text)

    def test_cli_refuses_existing_manifest_without_probing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("do not replace", encoding="utf-8")
            command = [sys.executable, "-m", "clipforge.editing.manifest", str(Path(directory) / "missing.mp4"),
                       "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output already exists", result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "do not replace")


if __name__ == "__main__":
    unittest.main()
