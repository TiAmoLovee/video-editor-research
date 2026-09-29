import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from clipforge.api import app
from clipforge.analysis.options import detector_options, parse_options
from clipforge.services.pipeline import process_video
from clipforge.storage.jobs import job_dir, get_job


class ShotOptionsTests(unittest.TestCase):
    def test_validation_and_pixel_conversion(self):
        self.assertIsNone(parse_options(None))
        value = parse_options('{"method":"robust","region":[0.1,0.2,0.9,0.8]}')
        self.assertEqual(detector_options(value, {'video': {'width': 1920, 'height': 1080}}),
                         {'method': 'robust', 'min_scene_len': 6, 'crop': (192, 216, 1728, 864)})
        self.assertEqual(detector_options({'method': 'robust'}, {}), {'method': 'robust', 'min_scene_len': 6})
        for text in ['null', '[]', '{}', '{"method":"invalid"}', '{"method":"robust","extra":1}',
                     '{"method":"robust","region":[false,0,1,1]}',
                     '{"method":"robust","region":[0,0,NaN,1]}',
                     '{"method":"robust","region":[0,0,2,1]}',
                     '{"method":"robust","region":[0,0,0.001,1]}']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_options(text)

    def test_upload_isolated_settings_and_worker_receives_normalized_region(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CLIPFORGE_DATA_DIR': directory}), TestClient(app) as client:
            settings = {'method': 'robust', 'region': [0.1, 0.2, 0.9, 0.8]}
            with patch('clipforge.routes.video.submit_video'):
                response = client.post('/tasks', files={'file': ('screen.mp4', b'video', 'video/mp4')},
                                       data={'shot_options': json.dumps(settings)})
                plain = client.post('/tasks', files={'file': ('plain.mp4', b'video', 'video/mp4')})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(plain.status_code, 202)
            task_id = response.json()['task_id']
            self.assertEqual(json.loads((job_dir(task_id)/'shot_options.json').read_text(encoding='utf-8')), settings)
            self.assertFalse((job_dir(plain.json()['task_id'])/'shot_options.json').exists())
            normalized = {'video': {'width': 1920, 'height': 1080}}
            with patch('clipforge.services.pipeline.probe_video', return_value={}), \
                 patch('clipforge.services.pipeline.normalize_metadata', return_value={}), \
                 patch('clipforge.services.pipeline.normalize_video', return_value=normalized), \
                 patch('clipforge.services.pipeline.detect_shots', side_effect=RuntimeError('stop-after-options')) as detector:
                with self.assertRaisesRegex(RuntimeError, 'stop-after-options'):
                    process_video(task_id)
            detector.assert_called_once_with(job_dir(task_id)/'normalized.mp4', 'ffprobe',
                                              method='robust', min_scene_len=6, crop=(192,216,1728,864))
            self.assertEqual(get_job(task_id)['status'], 'FAILED')

    def test_invalid_settings_do_not_create_jobs(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'CLIPFORGE_DATA_DIR': directory}), TestClient(app) as client:
            with patch('clipforge.routes.video.submit_video') as submit:
                response = client.post('/tasks', files={'file': ('screen.mp4', b'video', 'video/mp4')},
                                       data={'shot_options': '{"method":"robust","region":[0,0,2,1]}'})
            self.assertEqual(response.status_code, 422)
            submit.assert_not_called()
            self.assertFalse((Path(directory)/'jobs').exists())


if __name__ == '__main__':
    unittest.main()
