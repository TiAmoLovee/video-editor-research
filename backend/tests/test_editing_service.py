"""Task EDL persistence, source binding, cache references and legacy compatibility."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests.test_candidates import fixture
from backend.tests.test_edl import source_fixture
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.services import editing
from clipforge.storage import jobs


class EditingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"CLIPFORGE_DATA_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.task_id = str(uuid4())
        self.folder = jobs.job_dir(self.task_id)
        self.folder.mkdir(parents=True)
        self.media = b"mocked probe input, no model request"
        (self.folder / "normalized.mp4").write_bytes(self.media)
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis["media"]["normalized_sha256"] = hashlib.sha256(self.media).hexdigest()
        self.candidates = score_candidates(generate_candidates(analysis), analysis)
        self.source = source_fixture(id=f"normalized-{self.task_id}", ref="normalized.mp4",
                                     sha256=analysis["media"]["normalized_sha256"], total_frames=930)
        self.manifest = {"source": self.source, "profile": {
            "width": self.source["width"], "height": self.source["height"], "fps": self.source["fps"]}}
        for name, data in (("analysis.json", analysis), ("candidates.json", self.candidates)):
            (self.folder / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (self.folder / "result.zip").write_bytes(b"old ZIP unchanged")
        self.result = {"clip_count": 1, "total_frames": 930, "clips": [],
                       "files": {"analysis.json": "analysis.json", "candidates.json": "candidates.json",
                                 "result.zip": "result.zip"}}
        jobs.create_job(self.task_id, "Test.mp4", "source.mp4")
        jobs.claim_job(self.task_id)
        jobs.update_job(self.task_id, "SUCCEEDED", "done", 100, result=self.result)
        self.selected = [self.candidates["candidates"][-1]["id"], self.candidates["candidates"][0]["id"]]

    def create(self, task_id=None, selected=None, manifest=None):
        with patch.object(editing, "build_media_manifest", return_value=manifest or self.manifest):
            return editing.create_task_edl(task_id or self.task_id, selected or self.selected)

    def test_saved_edl_order_baseline_and_existing_results_unchanged(self):
        before = jobs.get_job(self.task_id)
        files = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        revision = self.create()
        saved = editing.load_task_edl(self.task_id, revision["revision_id"])
        self.assertEqual(saved["manifest"], self.manifest)
        self.assertEqual([c["provenance"]["candidate_id"] for c in saved["edl"]["clips"]], self.selected)
        self.assertEqual(jobs.get_job(self.task_id), before)
        self.assertTrue(all((self.folder / name).read_bytes() == content for name, content in files.items()))
        self.assertEqual(editing.revision_source(self.task_id, revision["revision_id"]),
                         (self.folder / "normalized.mp4").resolve())
        for name in editing.FILES:
            self.assertTrue(editing.revision_file(self.task_id, revision["revision_id"], name).is_file())

    def test_new_revisions_are_independent_and_read_from_saved_snapshot(self):
        first, second = self.create(), self.create(selected=list(reversed(self.selected)))
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        (self.folder / "candidates.json").write_text("invalid current ranking", encoding="utf-8")
        saved = editing.load_task_edl(self.task_id, first["revision_id"])
        self.assertEqual([c["provenance"]["candidate_id"] for c in saved["edl"]["clips"]], self.selected)

    def test_invalid_selection_rejected_before_expensive_probe(self):
        for selected in ([], [self.selected[0]] * 2, ["not-in-this-task"], [True], [" "]):
            with self.subTest(selected=selected), patch.object(editing, "build_media_manifest") as builder:
                with self.assertRaises(editing.ProjectError) as caught:
                    editing.create_task_edl(self.task_id, selected)
                self.assertEqual(caught.exception.status_code, 422)
                builder.assert_not_called()
        self.assertFalse((self.folder / "edits").exists())

    def test_changed_media_and_wrong_analysis_duration_are_rejected(self):
        wrong = deepcopy(self.manifest)
        wrong["source"]["sha256"] = "f" * 64
        with self.assertRaises(editing.ProjectError):
            self.create(manifest=wrong)
        wrong = deepcopy(self.manifest)
        wrong["source"]["total_frames"] += 2
        with self.assertRaises(editing.ProjectError):
            self.create(manifest=wrong)
        (self.folder / "normalized.mp4").write_bytes(b"replaced media")
        with self.assertRaises(editing.ProjectError):
            self.create()
        self.assertFalse((self.folder / "edits").exists())

    def test_probe_failure_publishes_nothing_and_masks_tool_details(self):
        with patch.object(editing, "build_media_manifest", side_effect=RuntimeError("private details")):
            with self.assertRaises(editing.ProjectError) as caught:
                editing.create_task_edl(self.task_id, self.selected)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private details", caught.exception.detail)
        self.assertFalse((self.folder / "edits").exists())

    def test_cache_hit_references_verified_origin_without_copying_media(self):
        cached_id = str(uuid4())
        cached = jobs.job_dir(cached_id)
        cached.mkdir()
        for name in ("analysis.json", "candidates.json"):
            (cached / name).write_bytes((self.folder / name).read_bytes())
        (cached / "cache.json").write_text(json.dumps({"hit": True, "reused_from_task_id": self.task_id}),
                                           encoding="utf-8")
        result = deepcopy(self.result)
        result["files"]["cache.json"] = "cache.json"
        jobs.create_job(cached_id, "cached.mp4", "source.mp4")
        jobs.claim_job(cached_id)
        jobs.update_job(cached_id, "SUCCEEDED", "done", 100, result=result)
        revision = self.create(task_id=cached_id)
        self.assertEqual(revision["source_task_id"], self.task_id)
        self.assertFalse((cached / "normalized.mp4").exists())
        self.assertEqual(editing.revision_source(cached_id, revision["revision_id"]),
                         (self.folder / "normalized.mp4").resolve())
        (self.folder / "normalized.mp4").write_bytes(b"changed origin")
        with self.assertRaises(editing.ProjectError):
            editing.revision_source(cached_id, revision["revision_id"])

    def test_missing_media_and_cache_cycles_never_use_raw_upload(self):
        (self.folder / "normalized.mp4").unlink()
        (self.folder / "source.mp4").write_bytes(self.media)
        with self.assertRaises(editing.ProjectError):
            self.create()
        result = deepcopy(self.result)
        result["files"]["cache.json"] = "cache.json"
        (self.folder / "cache.json").write_text(json.dumps({"hit": True, "reused_from_task_id": self.task_id}),
                                                encoding="utf-8")
        with patch.object(editing, "get_job", return_value={"status": "SUCCEEDED", "result": result}):
            with self.assertRaises(editing.ProjectError):
                self.create()
        self.assertFalse((self.folder / "edits").exists())

    def test_failed_publication_leaves_no_partial_revision(self):
        with patch.object(editing, "_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(editing.ProjectError):
                self.create()
        self.assertEqual(list((self.folder / "edits").iterdir()), [])

    def test_changed_revision_and_download_traversal_are_rejected(self):
        revision = self.create()
        for filename in ("../normalized.mp4", "revision.json", "normalized.mp4"):
            with self.assertRaises(editing.ProjectError) as caught:
                editing.revision_file(self.task_id, revision["revision_id"], filename)
            self.assertEqual(caught.exception.status_code, 404)
        saved = self.folder / "edits" / revision["revision_id"]
        (saved / "project.edl.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(editing.ProjectError) as caught:
            editing.load_task_edl(self.task_id, revision["revision_id"])
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(editing.ProjectError):
            editing.load_task_edl(self.task_id, "../bad")

    def test_unknown_and_unfinished_jobs_do_not_probe(self):
        with patch.object(editing, "build_media_manifest") as builder:
            with self.assertRaises(editing.ProjectError) as caught:
                editing.create_task_edl(str(uuid4()), self.selected)
            self.assertEqual(caught.exception.status_code, 404)
            with patch.object(editing, "get_job", return_value={"status": "RUNNING"}):
                with self.assertRaises(editing.ProjectError) as caught:
                    editing.create_task_edl(self.task_id, self.selected)
                self.assertEqual(caught.exception.status_code, 409)
            builder.assert_not_called()

    def test_verified_downloads_read_the_hashed_handle(self):
        revision = self.create()
        with editing.open_revision_file(self.task_id, revision['revision_id'], 'project.edl.json') as incoming:
            self.assertEqual(json.load(incoming)['duration_frames'], revision['duration_frames'])
        with editing.open_revision_source(self.task_id, revision['revision_id']) as incoming:
            if os.name == 'nt':
                with self.assertRaises(PermissionError):
                    (self.folder / 'normalized.mp4').write_bytes(b'replaced while streaming')
            self.assertEqual(incoming.read(), self.media)
        self.assertTrue(incoming.closed)
        (self.folder / 'normalized.mp4').write_bytes(b'replaced after streaming')
        with self.assertRaises(editing.ProjectError):
            with editing.open_revision_source(self.task_id, revision['revision_id']):
                self.fail('changed media must not be streamed')


if __name__ == "__main__":
    unittest.main()
