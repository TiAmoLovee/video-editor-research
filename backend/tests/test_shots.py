"""帧边界单元测试及可在 CI 运行的真实解码测试（不需要 FFmpeg）。"""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from clipforge.analysis.shots import _detect_frames, detect_shots, frames_to_shots


class ShotTests(unittest.TestCase):
    def test_intervals_use_exclusive_end_and_preserve_one_frame_tail(self):
        self.assertEqual(frames_to_shots([(0, 60), (60, 61)], 61),
                         [{"start": 0, "end": 2}, {"start": 2, "end": 61 / 30}])

    def test_incomplete_overlap_gap_empty_and_out_of_bounds_fail(self):
        for ranges in ([], [(0, 1)], [(0, 2), (1, 3)], [(0, 1), (2, 3)],
                       [(0, 0), (0, 3)], [(0, 4)], [(1, 3)]):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                frames_to_shots(ranges, 3)

    def test_invalid_settings_fail_before_media_access(self):
        for options in ({"threshold": float("nan")}, {"threshold": float("inf")},
                        {"threshold": 0}, {"threshold": True}, {"threshold": 256},
                        {"min_scene_len": 0}, {"min_scene_len": 1.5},
                        {"downscale": 0}, {"downscale": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                detect_shots("missing.mp4", **options)

    def test_missing_source_fails(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            detect_shots("never-created-file.mp4")

    def mocked_source(self, stack):
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        source = folder / "normalized.mp4"
        source.write_bytes(b"input identity")
        stack.enter_context(patch("clipforge.analysis.shots.probe_video", return_value={}))
        stack.enter_context(patch("clipforge.analysis.shots.normalize_metadata", return_value={
            "video": {"codec": "h264", "avg_frame_rate": "30/1", "stream_index": 0}}))
        stack.enter_context(patch("clipforge.analysis.shots.read_frame_count", return_value=90))
        return source

    def test_decode_shortfall_is_not_published_as_full_success(self):
        with ExitStack() as stack:
            source = self.mocked_source(stack)
            stack.enter_context(patch("clipforge.analysis.shots._detect_frames", return_value=([(0, 60)], 60)))
            with self.assertRaisesRegex(ValueError, "帧数不完整"):
                detect_shots(source)

    def test_non_normalized_input_is_rejected(self):
        with ExitStack() as stack:
            source = self.mocked_source(stack)
            stack.enter_context(patch("clipforge.analysis.shots.normalize_metadata", return_value={
                "video": {"codec": "h264", "avg_frame_rate": "25/1"}}))
            with self.assertRaisesRegex(ValueError, "归一化"):
                detect_shots(source)

    def test_output_records_identity_parameters_and_no_fake_audio_results(self):
        with ExitStack() as stack:
            source = self.mocked_source(stack)
            stack.enter_context(patch("clipforge.analysis.shots._detect_frames", return_value=([(0, 60), (60, 90)], 90)))
            result = detect_shots(source, threshold=30, min_scene_len=12, downscale=2)
        self.assertEqual(result["artifact_kind"], "shot_analysis")
        self.assertEqual(len(result["media"]["normalized_sha256"]), 64)
        self.assertEqual(result["cut_frames"], [60])
        self.assertEqual(result["analyzer"]["parameters"]["threshold"], 30)
        self.assertEqual(result["shots"][-1]["end"], 3)
        self.assertNotIn("words", result)
        self.assertNotIn("speech", result)


class RealDecoderTests(unittest.TestCase):
    def detect(self, segments, fps=30, threshold=27):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "中文 样例.avi"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 96))
            self.assertTrue(writer.isOpened(), "CI 必须具备 MJPG 测试视频编码能力")
            try:
                for count, level in segments:
                    for _ in range(count):
                        writer.write(np.full((96, 160, 3), level, dtype=np.uint8))
            finally:
                writer.release()
            result = _detect_frames(source, threshold, 15, 1)
            source.unlink()  # Windows 也必须能删除，确认检测后释放文件句柄。
            return result

    def test_real_hard_cuts_at_known_frames(self):
        ranges, decoded = self.detect([(30, 0), (30, 255), (30, 0)])
        self.assertEqual(decoded, 90)
        self.assertEqual(ranges, [(0, 30), (30, 60), (60, 90)])

    def test_no_cut_is_one_full_scene(self):
        self.assertEqual(self.detect([(45, 100)]), ([(0, 45)], 45))

    def test_single_frame_is_not_empty(self):
        self.assertEqual(self.detect([(1, 100)]), ([(0, 1)], 1))

    def test_threshold_changes_detection(self):
        ranges, decoded = self.detect([(30, 0), (30, 255), (30, 0)], threshold=255)
        self.assertEqual((ranges, decoded), ([(0, 90)], 90))

    def test_wrong_decoder_fps_fails(self):
        with self.assertRaisesRegex(ValueError, "不是 30 fps"):
            self.detect([(30, 0)], fps=25)

    def test_corrupt_media_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "corrupt.mp4"
            path.write_bytes(b"this is not a video")
            with self.assertRaises(Exception):
                _detect_frames(path, 27, 15, 1)

    def test_decoder_skipped_frame_is_not_hidden_by_final_position(self):
        from scenedetect import open_video
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "size-change.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (160, 96))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(3):
                    writer.write(np.zeros((96, 160, 3), dtype=np.uint8))
            finally:
                writer.release()
            video = open_video(str(path))
            read = video.read

            def damaged_read(*args, **kwargs):
                frame = read(*args, **kwargs)
                if isinstance(frame, np.ndarray) and video.frame_number == 2:
                    return frame[:48, :80]
                return frame

            with patch("scenedetect.open_video", return_value=video), patch.object(video, "read", side_effect=damaged_read):
                with self.assertRaisesRegex(ValueError, "未逐帧完整处理"):
                    _detect_frames(path, 27, 15, 1)


if __name__ == "__main__":
    unittest.main()
