"""Transcript seeking serves verified media and preserves task/cache isolation."""

from copy import deepcopy
import json
import unittest
from uuid import UUID, uuid4
from types import SimpleNamespace

from fastapi import HTTPException

from backend.tests import test_editing_service as fixtures
from backend.tests import test_verified_download_ranges as ranges
from clipforge.routes.task_media import task_source
from clipforge.storage import jobs


class TaskMediaTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)

    def request(self, task, headers=None):
        try:
            return ranges.VerifiedDownloadRangeTests.deliver(task_source(UUID(task)), headers)
        except HTTPException as error:
            return SimpleNamespace(status_code=error.status_code)

    def test_seek_range_without_an_edl_or_mutating_task(self):
        before = jobs.get_job(self.task_id)
        response = self.request(self.task_id, {"Range": "bytes=2-8"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.media[2:9])
        self.assertEqual(response.headers["content-range"], f"bytes 2-8/{len(self.media)}")
        self.assertEqual(jobs.get_job(self.task_id), before)
        self.assertFalse((self.folder / "edits").exists())
        response = self.request(self.task_id, {"Range": "bytes=9999-"})
        self.assertEqual(response.status_code, 416)

    def test_changed_media_and_invalid_transcript_are_rejected(self):
        (self.folder / "normalized.mp4").write_bytes(b"changed source")
        self.assertEqual(self.request(self.task_id).status_code, 409)
        (self.folder / "normalized.mp4").write_bytes(self.media)
        (self.folder / "analysis.json").write_text("invalid JSON", encoding="utf-8")
        self.assertEqual(self.request(self.task_id).status_code, 409)

    def test_cache_hit_uses_origin_bound_to_this_transcript(self):
        cached_id = str(uuid4())
        cached = jobs.job_dir(cached_id)
        cached.mkdir()
        (cached / "analysis.json").write_bytes((self.folder / "analysis.json").read_bytes())
        (cached / "cache.json").write_text(json.dumps({"hit": True, "reused_from_task_id": self.task_id}), encoding="utf-8")
        result = deepcopy(self.result)
        result["files"]["cache.json"] = "cache.json"
        jobs.create_job(cached_id, "cached.mp4", "source.mp4")
        jobs.claim_job(cached_id)
        jobs.update_job(cached_id, "SUCCEEDED", "done", 100, result=result)
        response = self.request(cached_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.media)
        self.assertFalse((cached / "normalized.mp4").exists())
        (cached / "cache.json").write_text(json.dumps({"hit": True, "reused_from_task_id": cached_id}), encoding="utf-8")
        (self.folder / "normalized.mp4").unlink()
        self.assertEqual(self.request(cached_id).status_code, 409)

    def test_unknown_and_unfinished_tasks(self):
        self.assertEqual(self.request(str(uuid4())).status_code, 404)
        task = str(uuid4())
        jobs.create_job(task, "new.mp4", "source.mp4")
        self.assertEqual(self.request(task).status_code, 409)
