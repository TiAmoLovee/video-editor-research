import os
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from api import app
from job_store import create_job, claim_job, update_job


class WebUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"CLIPFORGE_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_home_serves_page_with_download_controls(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('下载全部切片', response.text)

    def test_empty_history(self):
        response = self.client.get('/tasks')
        self.assertEqual(response.json()['items'], [])
        self.assertFalse(response.json()['has_more'])

    def test_history_paginates_and_hides_internal_paths(self):
        ids = []
        for number in range(3):
            task_id = str(uuid4())
            create_job(task_id, f'video{number}.mp4', 'source.mp4')
            ids.append(task_id)
        claim_job(ids[2])
        update_job(ids[2], 'SUCCEEDED', 'done', 100, result={'files': {}})
        first = self.client.get('/tasks?limit=2').json()
        self.assertTrue(first['has_more'])
        self.assertEqual(first['items'][0]['task_id'], ids[2])
        self.assertEqual(first['items'][0]['status'], 'SUCCEEDED')
        self.assertNotIn('source_key', first['items'][0])
        second = self.client.get('/tasks?limit=2&offset=2').json()
        self.assertFalse(second['has_more'])
        self.assertEqual(second['items'][0]['task_id'], ids[0])

    def test_invalid_pagination_rejected(self):
        for query in ('limit=0', 'limit=101', 'offset=-1'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/tasks?'+query).status_code, 422)


if __name__ == '__main__':
    unittest.main()
