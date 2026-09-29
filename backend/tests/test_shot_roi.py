"""ROI检测保留原时间轴，并排除区域外变化。"""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from clipforge.analysis.shots import _detect_frames, detect_shots
import test_shots


class ShotRoiTests(unittest.TestCase):
    def test_invalid_crop_is_rejected_before_media_access(self):
        for crop in ('1,2,3,4', [], [0, 0, 1], [False, 0, 1, 1],
                     [-1, 0, 1, 1], [2, 0, 1, 1], [0, 0, 0, 1], [0, 0, 1.2, 2]):
            with self.subTest(crop=crop), self.assertRaisesRegex(ValueError, 'crop'):
                detect_shots('missing.mp4', crop=crop)

    def test_crop_metadata_and_timebase_are_preserved(self):
        with ExitStack() as stack:
            source = test_shots.ShotTests().mocked_source(stack)
            detector = stack.enter_context(patch('clipforge.analysis.shots._detect_frames',
                                                 return_value=([(0, 30), (30, 90)], 90)))
            result = detect_shots(source, crop=[40, 48, 120, 96])
            detector.assert_called_once_with(source, 27.0, 15, 1, crop=(40, 48, 120, 96))
            self.assertEqual(result['cut_frames'], [30])
            self.assertEqual(result['shots'], [{'start': 0, 'end': 1}, {'start': 1, 'end': 3}])
            self.assertEqual(result['analyzer']['parameters']['crop_xyxy_exclusive'], [40, 48, 120, 96])

    def test_small_player_cut_and_large_surrounding_window_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'player.avi'
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (320, 192))
            self.assertTrue(writer.isOpened())
            try:
                for frame in range(90):
                    image = np.full((192, 320, 3), 255 if frame >= 60 else 0, np.uint8)
                    image[48:96, 40:120] = 255 if frame >= 30 else 0
                    writer.write(image)
            finally:
                writer.release()
            self.assertEqual(_detect_frames(path, 27, 15, 1), ([(0, 60), (60, 90)], 90))
            self.assertEqual(_detect_frames(path, 27, 15, 1, crop=(40, 48, 120, 96)),
                             ([(0, 30), (30, 90)], 90))
            self.assertEqual(_detect_frames(path, 27, 15, 1, crop=(0, 0, 320, 192)),
                             _detect_frames(path, 27, 15, 1))
            for crop, scale in [((0, 0, 321, 192), 1), ((0, 0, 1, 1), 2)]:
                with self.subTest(crop=crop), self.assertRaisesRegex(ValueError, 'crop'):
                    _detect_frames(path, 27, 15, scale, crop=crop)
            path.unlink()


if __name__ == '__main__':
    unittest.main()
