"""Task-scoped response previews and immutable adoption without worker/model work."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from pydantic import ValidationError

from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.editing.edl import validate_edl
from clipforge.routes import editing as routes
from clipforge.services import editing, editing_changes, editing_history
from clipforge.storage import jobs
from test_edl import source_fixture
from test_relation_event_plan import proposal
from test_response_candidates import fixture


class TaskResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"CLIPFORGE_DATA_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.task = str(uuid4())
        self.folder = jobs.job_dir(self.task)
        self.folder.mkdir(parents=True)
        self.media = b"immutable unit fixture; measured manifest is mocked"
        (self.folder/"normalized.mp4").write_bytes(self.media)
        self.analysis, original = fixture()
        self.analysis["media"]["normalized_sha256"] = hashlib.sha256(self.media).hexdigest()
        self.proposal = proposal(self.analysis, relations=original["relations"], cuts=original["boundary_hints"])
        self.old = score_candidates(generate_candidates(self.analysis), self.analysis)
        for name, body in (("analysis.json", self.analysis), ("candidates.json", self.old)):
            self.write(self.folder/name, body)
        (self.folder/"result.zip").write_bytes(b"original task download")
        result = {"clip_count": 0, "total_frames": 2160, "clips": [],
                  "files": {name: name for name in ("analysis.json", "candidates.json", "result.zip")}}
        jobs.create_job(self.task, "source.mp4", "source.mp4")
        jobs.claim_job(self.task)
        jobs.update_job(self.task, "SUCCEEDED", "done", 100, result=result)
        source = source_fixture(id=f"normalized-{self.task}", ref="normalized.mp4",
                                sha256=self.analysis["media"]["normalized_sha256"], total_frames=2160)
        self.manifest = {"source": source, "profile": {k: source[k] for k in ("width", "height", "fps")}}
        no_model = patch("clipforge.decision.llm.http_transport", side_effect=AssertionError("provider called"))
        no_model.start()
        self.addCleanup(no_model.stop)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def preview(self, task=None, source=None):
        return editing.preview_task_response_candidates(task or self.task, self.proposal if source is None else source)

    def adopt(self, preview=None, ids=None, **kwargs):
        preview = preview or self.preview()
        selected = ids if ids is not None else [c["id"] for c in reversed(preview["candidates"]["candidates"])]
        with patch.object(editing, "build_media_manifest", return_value=self.manifest):
            return editing.create_task_edl(self.task, selected, response_proposal=self.proposal,
                response_preview_sha256=preview["preview_sha256"], **kwargs)

    def test_preview_is_deterministic_read_only_and_explicitly_unverified(self):
        original = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        job = jobs.get_job(self.task)
        with patch.object(editing, "build_media_manifest") as probe:
            first, second = self.preview(), self.preview()
        self.assertEqual(first, second)
        self.assertEqual(first["task_id"], self.task)
        self.assertEqual(first["candidates"]["candidate_count"], 2)
        self.assertTrue(first["requires_review"])
        self.assertFalse(first["media_verified"])
        self.assertFalse(first["semantic_completeness_verified"])
        self.assertEqual(first["model_requests"], 0)
        probe.assert_not_called()
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.folder.iterdir()})
        self.assertEqual(jobs.get_job(self.task), job)

    def test_response_adoption_keeps_order_snapshots_and_original_results(self):
        preview = self.preview()
        original = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        job = jobs.get_job(self.task)
        record = self.adopt(preview)
        saved = editing.load_task_edl(self.task, record["revision_id"])
        self.assertEqual([(c["source_in_frame"], c["source_out_frame"]) for c in saved["edl"]["clips"]],
                         [(984, 1872), (24, 912)])
        snapshot = json.loads((self.folder/"edits"/record["revision_id"]/"candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot, preview["candidates"])
        self.assertEqual(jobs.get_job(self.task), job)
        self.assertTrue(all((self.folder/name).read_bytes() == body for name, body in original.items()))
        page = editing_history.list_task_edls(self.task)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["unavailable_count"], 0)
        self.assertEqual(page["items"][0]["revision_id"], record["revision_id"])

    def test_missing_or_changed_preview_rejected_before_media_probe(self):
        for token in (None, "0"*64, True):
            with self.subTest(token=token), patch.object(editing, "build_media_manifest") as probe:
                with self.assertRaises(editing.ProjectError) as raised:
                    editing.create_task_edl(self.task, ["unused"], response_proposal=self.proposal,
                                            response_preview_sha256=token)
                self.assertEqual(raised.exception.status_code, 409)
                probe.assert_not_called()
        self.assertFalse((self.folder/"edits").exists())

    def test_changed_hypothesis_invalidates_preview_even_if_selected_range_is_same(self):
        preview = self.preview()
        self.proposal["relations"][0][-1] = 3
        self.assertNotEqual(self.preview()["preview_sha256"], preview["preview_sha256"])
        with self.assertRaises(editing.ProjectError):
            self.adopt(preview)
        self.assertFalse((self.folder/"edits").exists())

    def test_preview_is_bound_to_task_even_for_identical_cached_analysis(self):
        other = str(uuid4())
        jobs.job_dir(other).mkdir()
        self.write(jobs.job_dir(other)/"analysis.json", self.analysis)
        jobs.create_job(other, "same-source.mp4", "source.mp4")
        jobs.claim_job(other)
        jobs.update_job(other, "SUCCEEDED", "done", 100, result={"files": {"analysis.json": "analysis.json"}})
        self.assertNotEqual(self.preview(other)["preview_sha256"], self.preview()["preview_sha256"])

    def test_bad_source_proposal_and_unfinished_task_are_rejected_without_writes(self):
        wrong = deepcopy(self.proposal)
        wrong["normalized_sha256"] = "f"*64
        with self.assertRaises(editing.ProjectError):
            self.preview(source=wrong)
        unfinished = str(uuid4())
        jobs.create_job(unfinished, "queued.mp4", "source.mp4")
        with self.assertRaises(editing.ProjectError) as raised:
            self.preview(unfinished)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse((self.folder/"edits").exists())

    def test_cached_task_reuses_verified_normalized_source_without_copying_media(self):
        other = str(uuid4())
        folder = jobs.job_dir(other)
        folder.mkdir()
        self.write(folder/"analysis.json", self.analysis)
        self.write(folder/"cache.json", {"hit": True, "reused_from_task_id": self.task})
        jobs.create_job(other, "same-source.mp4", "source.mp4")
        jobs.claim_job(other)
        jobs.update_job(other, "SUCCEEDED", "done", 100, result={"files": {
            "analysis.json": "analysis.json", "cache.json": "cache.json"}})
        preview = self.preview(other)
        ids = [c["id"] for c in preview["candidates"]["candidates"]]
        with patch.object(editing, "build_media_manifest", return_value=self.manifest):
            record = editing.create_task_edl(other, ids, response_proposal=self.proposal,
                                            response_preview_sha256=preview["preview_sha256"])
        self.assertEqual(record["source_task_id"], self.task)
        self.assertEqual(editing.revision_source(other, record["revision_id"]), (self.folder/"normalized.mp4").resolve())
        self.assertFalse((folder/"normalized.mp4").exists())

    def test_media_change_after_text_preview_prevents_adoption(self):
        preview = self.preview()
        (self.folder/"normalized.mp4").write_bytes(b"different media after preview")
        with self.assertRaises(editing.ProjectError) as raised:
            self.adopt(preview)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse((self.folder/"edits").exists())

    def test_new_ids_do_not_silently_fall_back_to_old_candidate_rankings(self):
        selected = [self.preview()["candidates"]["candidates"][0]["id"]]
        with patch.object(editing, "build_media_manifest") as probe:
            with self.assertRaises(editing.ProjectError) as raised:
                editing.create_task_edl(self.task, selected)
            self.assertEqual(raised.exception.status_code, 422)
            probe.assert_not_called()
        self.assertFalse((self.folder/"edits").exists())

    def test_legacy_and_response_versions_coexist_without_changing_legacy_selection(self):
        old_id = self.old["candidates"][0]["id"]
        with patch.object(editing, "build_media_manifest", return_value=self.manifest):
            old = editing.create_task_edl(self.task, [old_id])
        new = self.adopt()
        self.assertEqual(editing_history.list_task_edls(self.task)["total"], 2)
        self.assertEqual(editing.load_task_edl(self.task, old["revision_id"])["edl"]["clips"][0]["provenance"]["candidate_id"], old_id)
        self.assertNotEqual(old["revision_id"], new["revision_id"])

    def test_saved_response_version_survives_changed_current_rankings(self):
        record = self.adopt()
        (self.folder/"candidates.json").write_text("changed current ranking", encoding="utf-8")
        saved = editing.load_task_edl(self.task, record["revision_id"])
        self.assertEqual(saved["edl"]["duration_frames"], 1776)

    def test_rehashed_native_edl_cannot_hide_a_shortened_response(self):
        selected = [self.preview()["candidates"]["candidates"][0]["id"]]
        record = self.adopt(ids=selected)
        folder = self.folder/"edits"/record["revision_id"]
        edl = json.loads((folder/"project.edl.json").read_text(encoding="utf-8"))
        edl["clips"][0]["source_out_frame"] -= 1
        edl["clips"][0]["timeline_out_frame"] -= 1
        edl["duration_frames"] -= 1
        validate_edl(edl)
        self.write(folder/"project.edl.json", edl)
        record["files"]["project.edl.json"] = editing._digest(folder/"project.edl.json")
        self.write(folder/"revision.json", record)
        with self.assertRaises(editing.ProjectError) as raised:
            editing.load_task_edl(self.task, record["revision_id"])
        self.assertEqual(raised.exception.status_code, 409)

    def test_intentional_manual_edits_still_use_separate_version_and_effort_record(self):
        selected = [self.preview()["candidates"]["candidates"][0]["id"]]
        record = self.adopt(ids=selected)
        revised = editing_changes.create_task_edit(self.task, record["revision_id"], [{
            "candidate_id": selected[0], "source_in_frame": 24, "source_out_frame": 911}])
        saved = editing.load_task_edl(self.task, revised["revision_id"])
        self.assertEqual(saved["edl"]["duration_frames"], 887)
        self.assertIn("edit_record", saved)
        self.assertEqual(editing.load_task_edl(self.task, record["revision_id"])["edl"]["duration_frames"], 888)

    def test_saved_response_selection_can_use_existing_full_frame_portrait_flow(self):
        record = self.adopt()
        portrait = editing.create_task_portrait(self.task, record["revision_id"], mode="fit_pad")
        saved = editing.load_task_edl(self.task, portrait["revision_id"])
        self.assertEqual(saved["edl"]["schema_version"], "0.3.0")
        self.assertEqual(saved["edl"]["duration_frames"], 1776)
        self.assertEqual([(c["source_in_frame"], c["source_out_frame"]) for c in saved["edl"]["clips"]],
                         [(984, 1872), (24, 912)])

    def test_routes_validate_payloads_and_expose_selection_location(self):
        task = UUID(self.task)
        preview_response = routes.preview_response_candidates(task, routes.PreviewResponseRequest(proposal=self.proposal))
        self.assertEqual(preview_response.headers["cache-control"], "no-store")
        preview = json.loads(preview_response.body)
        ids = [c["id"] for c in preview["candidates"]["candidates"]]
        with patch.object(editing, "build_media_manifest", return_value=self.manifest):
            response = routes.create_response_edl(task, routes.CreateResponseEdlRequest(
                proposal=self.proposal, preview_sha256=preview["preview_sha256"], selected_ids=ids))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        revision_id = json.loads(response.body)["revision_id"]
        self.assertEqual(response.headers["location"], f"/tasks/{self.task}/edl/{revision_id}")
        self.assertEqual(routes.get_edl(task, UUID(revision_id)).status_code, 200)
        for invalid in ([], [ids[0]]*2, [True]):
            with self.subTest(ids=invalid), self.assertRaises(ValidationError):
                routes.CreateResponseEdlRequest(proposal=self.proposal, preview_sha256=preview["preview_sha256"], selected_ids=invalid)
        with self.assertRaises(ValidationError):
            routes.PreviewResponseRequest(proposal=self.proposal, api_key="never accepted")


if __name__ == "__main__":
    unittest.main()
