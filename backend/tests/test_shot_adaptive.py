"""实际解码验证局部变化率检测，不依赖人工样本切点驱动检测。"""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import test_shots

from clipforge.analysis.shots import _detect_frames, detect_shots


class AdaptiveShotTests(unittest.TestCase):
    def test_bad_settings_fail_before_media_access(self):
        for options in ({'method': 'unknown'}, {'adaptive_threshold': float('nan')},
                        {'adaptive_threshold': True}, {'min_content_val': 0},
                        {'min_content_val': float('inf')}, {'window_width': 0},
                        {'window_width': 31}, {'window_width': 2.5}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                detect_shots('missing.mp4', **options)

    def test_metadata_identifies_actual_method(self):
        with ExitStack() as stack:
            source = test_shots.ShotTests().mocked_source(stack)
            detector = stack.enter_context(patch('clipforge.analysis.shots._detect_frames',
                                                 return_value=([(0, 30), (30, 90)], 90)))
            result = detect_shots(source, method='adaptive', min_scene_len=6)
            # detect_shots expands Windows short paths before calling the detector.
            detector.assert_called_once_with(source.resolve(), 27.0, 6, 1, crop=None, method='adaptive',
                                             adaptive_threshold=3.0, min_content_val=15.0, window_width=2)
            self.assertEqual(result['analyzer']['tool'], 'PySceneDetect.AdaptiveDetector')
            parameters = result['analyzer']['parameters']
            self.assertNotIn('threshold', parameters)
            self.assertNotIn('filter_mode', parameters)
            self.assertEqual(parameters['adaptive_threshold'], 3.0)
            self.assertEqual(parameters['window_width_frames'], 2)
            self.assertEqual(result['cut_frames'], [30])

    def write_video(self, path, frames):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (160, 96))
        self.assertTrue(writer.isOpened())
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()

    def test_real_cuts_and_short_scene_preserve_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'short.avi'
            frames = [np.full((96, 160, 3), value, np.uint8)
                      for count, value in [(30, 0), (8, 255), (30, 0)] for _ in range(count)]
            self.write_video(path, frames)
            self.assertEqual(_detect_frames(path, 27, 6, 1, method='adaptive'),
                             ([(0, 30), (30, 38), (38, 68)], 68))
            path.unlink()

    def test_continuous_texture_pan_remains_one_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pan.avi'
            rng = np.random.default_rng(42)
            gray = (rng.integers(0, 2, (96, 160), dtype=np.uint8) * 255)
            pattern = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            self.write_video(path, [np.roll(pattern, n * 8, axis=1) for n in range(90)])
            self.assertEqual(_detect_frames(path, 27, 15, 1, method='adaptive'), ([(0, 90)], 90))


if __name__ == '__main__':
    unittest.main()
