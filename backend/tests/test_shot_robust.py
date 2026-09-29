from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import test_shots

from clipforge.analysis.robust_shots import detect_robust_frames, select_cuts
from clipforge.analysis.shots import detect_shots


class RobustShotTests(unittest.TestCase):
    def write(self, path, frames):
        height, width = frames[0].shape[:2]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (width, height))
        self.assertTrue(writer.isOpened())
        try:
            for frame in frames:
                writer.write(frame)
        finally:
            writer.release()

    def test_content_change_with_stationary_exterior_survives_and_window_change_is_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'screen.avi'
            frames = []
            for n in range(90):
                frame = np.full((192, 320, 3), 180 if n >= 60 else 0, np.uint8)
                frame[48:144, 80:240] = 220 if 30 <= n < 60 else 0
                frames.append(frame)
            self.write(path, frames)
            ranges, decoded, suppressed = detect_robust_frames(path, 15, (80, 48, 240, 144))
            self.assertEqual(ranges, [(0, 30), (30, 90)])
            self.assertEqual(decoded, 90)
            self.assertEqual(suppressed, [60])
            path.unlink()

    def test_no_roi_never_silently_filters_full_frame_cut(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'full.avi'
            self.write(path, [np.full((96, 160, 3), 200 if n >= 30 else 0, np.uint8) for n in range(31)])
            self.assertEqual(detect_robust_frames(path, 15), ([(0, 30), (30, 31)], 31, []))
            self.assertEqual(detect_robust_frames(path, 15, (0, 0, 160, 96)), ([(0, 30), (30, 31)], 31, []))
            with self.assertRaisesRegex(ValueError, 'crop'):
                detect_robust_frames(path, 15, (0, 0, 161, 96))
            path.unlink()

    def test_gradual_brightness_and_single_frame_do_not_create_cuts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'gradual.avi'
            self.write(path, [np.full((96, 160, 3), n*2, np.uint8) for n in range(90)])
            self.assertEqual(detect_robust_frames(path, 15), ([(0, 90)], 90, []))
            self.write(path, [np.zeros((96, 160, 3), np.uint8)])
            self.assertEqual(detect_robust_frames(path, 15), ([(0, 1)], 1, []))

    def test_selection_enforces_minimum_spacing_and_preserves_end_boundary(self):
        rows = [[0., 0., 0.] for _ in range(61)]
        rows[5][0], rows[30][0], rows[38][0], rows[60][0] = 30., 30., 50., 40.
        self.assertEqual(select_cuts(rows, 15), ([38, 60], []))
        self.assertEqual(select_cuts(rows, 6), ([30, 38, 60], []))

    def test_decode_size_change_and_skipped_position_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'damaged.avi'
            self.write(path, [np.zeros((96, 160, 3), np.uint8) for _ in range(3)])
            capture = cv2.VideoCapture(str(path))
            real_read = capture.read
            calls = 0
            def damaged_read():
                nonlocal calls
                ok, frame = real_read()
                calls += 1
                return (ok, frame[:48, :80]) if calls == 2 else (ok, frame)
            class Wrapper:
                def isOpened(self): return capture.isOpened()
                def get(self, name): return capture.get(name)
                def read(self): return damaged_read()
                def release(self): capture.release()
            with patch('cv2.VideoCapture', return_value=Wrapper()):
                with self.assertRaisesRegex(ValueError, '尺寸'):
                    detect_robust_frames(path, 15)
            path.unlink()

    def test_public_output_records_suppression_and_rejects_short_decode(self):
        with ExitStack() as stack:
            source = test_shots.ShotTests().mocked_source(stack)
            detector = stack.enter_context(patch('clipforge.analysis.robust_shots.detect_robust_frames',
                                                 return_value=([(0, 30), (30, 90)], 90, [60])))
            output = detect_shots(source, method='robust', crop=(0, 0, 40, 40))
            self.assertEqual(output['analyzer']['tool'], 'ClipForge.RobustColorDetector')
            self.assertEqual(output['diagnostics']['context_guard_suppressed_frames'], [60])
            self.assertEqual(output['cut_frames'], [30])
            self.assertEqual(output['media']['total_frames'], 90)
            self.assertNotIn('threshold', output['analyzer']['parameters'])
            detector.return_value = ([(0, 60)], 60, [])
            with self.assertRaisesRegex(ValueError, '帧数不完整'):
                detect_shots(source, method='robust')
        with self.assertRaisesRegex(ValueError, 'downscale'):
            detect_shots('missing.mp4', method='robust', downscale=2)


if __name__ == '__main__':
    unittest.main()
