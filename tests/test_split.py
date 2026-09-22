"""切片边界和文件保护测试，不启动 FFmpeg。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from split import build_plan, split_video


class SplitTests(unittest.TestCase):
    def test_short_video_is_one_clip(self):
        plan = build_plan(200)
        self.assertEqual(len(plan), 1)
        self.assertEqual((plan[0]["start_frame"], plan[0]["end_frame"]), (0, 200))

    def test_exact_30_seconds_has_no_empty_tail(self):
        plan = build_plan(900)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["frame_count"], 900)

    def test_one_frame_after_boundary_is_preserved(self):
        self.assertEqual([p["frame_count"] for p in build_plan(901)], [900, 1])

    def test_real_sample_has_two_clips(self):
        self.assertEqual([p["frame_count"] for p in build_plan(1238)], [900, 338])

    def test_five_minutes_has_ten_clips(self):
        plan = build_plan(9000)
        self.assertEqual(len(plan), 10)
        self.assertTrue(all(p["frame_count"] == 900 for p in plan))

    def test_no_gaps_or_overlaps(self):
        plan = build_plan(9031)
        self.assertEqual(plan[0]["start_frame"], 0)
        self.assertEqual(plan[-1]["end_frame"], 9031)
        self.assertEqual(sum(p["frame_count"] for p in plan), 9031)
        self.assertTrue(all(a["end_frame"] == b["start_frame"] for a, b in zip(plan, plan[1:])))

    def test_invalid_counts_are_rejected(self):
        for value in (0, -1, 900.5, True, "900"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_plan(value)

    def test_existing_directory_is_protected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            source.write_bytes(b"source")
            target = Path(folder) / "clips"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError), patch("split.probe_video") as probe:
                split_video(str(source), str(target))
            probe.assert_not_called()
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
