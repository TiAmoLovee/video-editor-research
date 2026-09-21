import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient

from api import app
import job_store as store
from queue_client import QueueUnavailable
from video_pipeline import process_video


class VideoJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"CLIPFORGE_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def submit(self, name="sample.mp4", data=b"video placeholder"):
        with patch("video_api.submit_video") as enqueue:
            response = self.client.post("/tasks", files={"file": (name, data, "video/mp4")})
        return response, enqueue

    def test_upload_creates_record_and_enqueues_without_processing(self):
        response, enqueue = self.submit()
        self.assertEqual(response.status_code, 202)
        task_id = response.json()["task_id"]
        enqueue.assert_called_once_with(task_id)
        job = store.get_job(task_id)
        self.assertEqual(job["status"], "QUEUED")
        self.assertEqual((store.job_dir(task_id) / "source.mp4").read_bytes(), b"video placeholder")
        self.assertFalse((store.job_dir(task_id) / "normalized.mp4").exists())
        self.assertEqual(response.headers["location"], f"/tasks/{task_id}")

    def test_user_filename_cannot_select_storage_path(self):
        response, _ = self.submit("../../outside.mp4")
        job = store.get_job(response.json()["task_id"])
        self.assertEqual(job["source_name"], "outside.mp4")
        self.assertEqual(job["source_key"], "source.mp4")
        self.assertFalse((Path(self.temp.name) / "outside.mp4").exists())

    def test_bad_extension_and_empty_file_are_rejected(self):
        for name, data, expected in (("test.txt", b"x", 415), ("empty.mp4", b"", 422)):
            with self.subTest(name=name):
                response, enqueue = self.submit(name, data)
                self.assertEqual(response.status_code, expected)
                enqueue.assert_not_called()

    def test_size_limit_prevents_enqueue(self):
        with patch("video_api.MAX_UPLOAD_BYTES", 4):
            response, enqueue = self.submit(data=b"12345")
        self.assertEqual(response.status_code, 413)
        enqueue.assert_not_called()

    def test_unknown_job_returns_404_and_invalid_id_422(self):
        self.assertEqual(self.client.get(f"/tasks/{uuid4()}").status_code, 404)
        self.assertEqual(self.client.get("/tasks/not-a-uuid").status_code, 422)

    def test_unfinished_job_cannot_download(self):
        response, _ = self.submit()
        task_id = response.json()["task_id"]
        result = self.client.get(f"/tasks/{task_id}/files/result.zip")
        self.assertEqual(result.status_code, 409)

    def test_uncertain_submission_keeps_record_for_late_worker(self):
        with patch("video_api.submit_video", side_effect=QueueUnavailable()):
            response = self.client.post("/tasks", files={"file": ("v.mp4", b"abc")})
        self.assertEqual(response.status_code, 503)
        task_id = response.json()["task_id"]
        self.assertEqual(store.get_job(task_id)["status"], "SUBMISSION_UNKNOWN")
        self.assertTrue(store.claim_job(task_id))
        self.assertFalse(store.claim_job(task_id))

    def test_late_submission_error_does_not_overwrite_worker_state(self):
        response, _ = self.submit()
        task_id = response.json()["task_id"]
        store.claim_job(task_id)
        store.submission_unknown(task_id)
        self.assertEqual(store.get_job(task_id)["status"], "RUNNING")

    def test_pipeline_failure_is_persisted_without_publishing_download(self):
        response, _ = self.submit()
        task_id = response.json()["task_id"]
        with patch("video_pipeline.probe_video", side_effect=ValueError("invalid private path")):
            with self.assertLogs("video_pipeline", level="ERROR"), self.assertRaises(ValueError):
                process_video(task_id)
        result = self.client.get(f"/tasks/{task_id}").json()
        self.assertEqual(result["status"], "FAILED")
        self.assertIsNone(result["result"])
        self.assertNotIn("private path", result["error"])
        self.assertFalse(store.claim_job(task_id))

    def test_success_packages_clips_and_rejects_unlisted_files(self):
        response, _ = self.submit()
        task_id = response.json()["task_id"]
        plan = {"total_frames": 1, "clips": [{"file": "clip_001.mp4", "frame_count": 1}]}

        def normalize(source, output, *args):
            Path(output).write_bytes(b"normalized")
            return {"video": {"codec": "h264"}}

        def split(source, output, *args):
            folder = Path(output)
            folder.mkdir()
            (folder / "clip_001.mp4").write_bytes(b"clip")
            (folder / "clip_plan.json").write_text(json.dumps(plan), encoding="utf-8")
            return plan

        with patch("video_pipeline.probe_video", return_value={}), \
             patch("video_pipeline.normalize_metadata", return_value={}), \
             patch("video_pipeline.normalize_video", side_effect=normalize), \
             patch("video_pipeline.split_video", side_effect=split):
            process_video(task_id)
        job = self.client.get(f"/tasks/{task_id}").json()
        self.assertEqual(job["status"], "SUCCEEDED")
        self.assertEqual(job["progress"], 100)
        archive = self.client.get(job["result"]["downloads"]["result.zip"])
        self.assertEqual(archive.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            self.assertEqual(zipped.read("clip_001.mp4"), b"clip")
            self.assertIsNone(zipped.testzip())
        self.assertEqual(self.client.get(f"/tasks/{task_id}/files/source.mp4").status_code, 404)
        self.assertTrue(process_video(task_id)["skipped"])
        # 新建数据库连接和 HTTP 客户端依然能查询到完成记录。
        with TestClient(app) as new_client:
            self.assertEqual(new_client.get(f"/tasks/{task_id}").json()["status"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
