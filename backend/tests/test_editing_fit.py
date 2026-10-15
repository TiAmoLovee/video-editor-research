"""Fit-pad revisions retain native snapshots, bound parent and exact cuts."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

from backend.tests import test_editing_service as fixtures
from backend.tests import test_portrait_service as portrait_fixtures
from clipforge.editing.edl import create_portrait_edl
from clipforge.routes import editing as routes
from clipforge.services import editing, editing_changes
from clipforge.storage import jobs


class EditingFitTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.original = fixtures.EditingServiceTests.create(self)
        self.parent_id = self.original["revision_id"]
        self.parent_folder = self.folder / "edits" / self.parent_id

    def create(self, parent_id=None):
        return editing.create_task_portrait(self.task_id, parent_id or self.parent_id, mode="fit_pad")

    def tamper(self, record, filename, value):
        portrait_fixtures.PortraitServiceTests.tamper_record(self, record, filename, value)

    def test_fit_revision_preserves_parent_source_snapshots_and_job_without_probe(self):
        before = jobs.get_job(self.task_id)
        files = {path.name: path.read_bytes() for path in self.parent_folder.iterdir()}
        original = editing.load_task_edl(self.task_id, self.parent_id)
        with patch.object(editing, "build_media_manifest") as probe:
            record = self.create()
            saved = editing.load_task_edl(self.task_id, record["revision_id"])
        probe.assert_not_called()
        self.assertEqual(saved["lineage_depth"], 2)
        self.assertEqual(saved["edl"], create_portrait_edl(original["edl"], mode="fit_pad"))
        self.assertEqual(saved["manifest"], original["manifest"])
        self.assertEqual(record["derivation"], {"kind": "fit_pad_9_16", "parent_revision_id": self.parent_id,
                                               "parent_edl_file_sha256": self.original["files"]["project.edl.json"]})
        for filename in editing.FILES - {"project.edl.json"}:
            self.assertEqual(record["files"][filename], self.original["files"][filename])
        self.assertEqual(jobs.get_job(self.task_id), before)
        self.assertEqual(files, {path.name: path.read_bytes() for path in self.parent_folder.iterdir()})
        with editing.open_revision_source(self.task_id, record["revision_id"]) as source:
            self.assertEqual(source.read(), self.media)

    def test_schema_kind_parent_hash_loop_and_target_changes_rejected_after_rehash(self):
        for mutation in ("kind", "parent_hash", "loop", "output_profile"):
            record = self.create()
            changed = deepcopy(record)
            if mutation == "kind":
                changed["derivation"]["kind"] = "center_crop_9_16"
            elif mutation == "parent_hash":
                changed["derivation"]["parent_edl_file_sha256"] = "0" * 64
            elif mutation == "loop":
                changed["derivation"]["parent_revision_id"] = record["revision_id"]
            else:
                parent = editing.load_task_edl(self.task_id, self.parent_id)["edl"]
                changed_project = create_portrait_edl(parent, 90, 160, mode="fit_pad")
                self.tamper(record, "project.edl.json", changed_project)
            if mutation != "output_profile":
                self.tamper(record, "revision.json", changed)
            with self.subTest(mutation=mutation), self.assertRaises(editing.ProjectError) as caught:
                editing.load_task_edl(self.task_id, record["revision_id"])
            self.assertEqual(caught.exception.status_code, 409)

    def test_existing_center_crop_route_and_fitted_parent_are_not_reinterpreted(self):
        center = editing.create_task_portrait(self.task_id, self.parent_id)
        self.assertEqual(editing.load_task_edl(self.task_id, center["revision_id"])["edl"]["schema_version"], "0.2.0")
        fit = self.create()
        for parent in (center["revision_id"], fit["revision_id"]):
            with self.subTest(parent=parent), self.assertRaises(editing.ProjectError):
                self.create(parent)
        self.assertEqual(len(list(self.parent_folder.parent.iterdir())), 3)

    def test_manual_edits_before_and_after_fit_preserve_adoption_baseline(self):
        saved = editing.load_task_edl(self.task_id, self.parent_id)
        requests = [{"candidate_id": clip["provenance"]["candidate_id"],
                     "source_in_frame": clip["source_in_frame"], "source_out_frame": clip["source_out_frame"]}
                    for clip in reversed(saved["edl"]["clips"])]
        manual = editing_changes.create_task_edit(self.task_id, self.parent_id, requests, editing_elapsed_ms=1000)
        fitted = self.create(manual["revision_id"])
        loaded = editing.load_task_edl(self.task_id, fitted["revision_id"])
        self.assertEqual(loaded["lineage_depth"], 3)
        self.assertEqual(loaded["edit_record"]["baseline_revision_id"], self.parent_id)
        self.assertEqual(loaded["edit_record"]["origin"], "automatic_fit_pad_no_manual_operation")
        self.assertEqual(loaded["edit_record"]["step_effort"]["adjusted_clip_count"], 0)
        after = editing_changes.create_task_edit(self.task_id, fitted["revision_id"], list(reversed(requests)))
        final = editing.load_task_edl(self.task_id, after["revision_id"])
        self.assertEqual(final["lineage_depth"], 4)
        self.assertEqual(final["edl"]["framing"], loaded["edl"]["framing"])
        self.assertEqual(final["edl"]["schema_version"], "0.3.0")
        self.assertEqual(final["edit_record"]["baseline_revision_id"], self.parent_id)

    def test_invalid_mode_depth_and_failed_publish_leave_existing_versions_intact(self):
        with self.assertRaises(editing.ProjectError) as caught:
            editing.create_task_portrait(self.task_id, self.parent_id, mode="blur")
        self.assertEqual(caught.exception.status_code, 422)
        saved = editing.load_task_edl(self.task_id, self.parent_id)
        with patch.object(editing, "load_task_edl", return_value={**saved, "lineage_depth": 16}):
            with self.assertRaises(editing.ProjectError) as caught:
                self.create()
        self.assertEqual(caught.exception.status_code, 422)
        with patch.object(editing, "_write_json", side_effect=OSError("private disk failure")):
            with self.assertRaises(editing.ProjectError) as caught:
                self.create()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private", caught.exception.detail)
        self.assertEqual([path.name for path in self.parent_folder.parent.iterdir()], [self.parent_id])


class EditingFitRouteTests(unittest.TestCase):
    def test_new_route_requests_explicit_fit_and_preserves_existing_route_default(self):
        task = UUID("00000000-0000-4000-8000-000000000001")
        revision = UUID("00000000-0000-4000-8000-000000000002")
        record = {"revision_id": str(revision), "task_id": str(task)}
        with patch.object(routes.editing_service, "create_task_portrait", return_value=record) as create:
            response = routes.create_portrait_fit(task, revision)
        create.assert_called_once_with(str(task), str(revision), mode="fit_pad")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.body), record)
        self.assertEqual(response.headers["location"], f"/tasks/{task}/edl/{revision}")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch.object(routes.editing_service, "create_task_portrait", return_value=record) as create:
            routes.create_portrait(task, revision)
        create.assert_called_once_with(str(task), str(revision))

    def test_new_route_preserves_safe_service_errors(self):
        task = UUID("00000000-0000-4000-8000-000000000001")
        with patch.object(routes.editing_service, "create_task_portrait", side_effect=editing.ProjectError(409, "safe")):
            with self.assertRaises(HTTPException) as caught:
                routes.create_portrait_fit(task, task)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, "safe")


if __name__ == "__main__":
    unittest.main()
