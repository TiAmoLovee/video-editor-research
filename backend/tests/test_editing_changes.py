"""Saved manual edits preserve source evidence and measure bound net/step changes."""

from copy import deepcopy
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests import test_editing_service as fixtures
from clipforge.editing.edl import seconds_to_frame
from clipforge.services import editing, editing_changes
from clipforge.storage import jobs


class EditingChangesServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)

    def create(self, selected=None):
        return fixtures.EditingServiceTests.create(self, selected=selected)

    def saved(self, revision):
        return editing.load_task_edl(self.task_id, revision["revision_id"])

    def folder_for(self, revision):
        return self.folder / "edits" / revision["revision_id"]

    def requests(self, saved):
        return [{"candidate_id": clip["provenance"]["candidate_id"],
                 "source_in_frame": clip["source_in_frame"],
                 "source_out_frame": clip["source_out_frame"]}
                for clip in saved["edl"]["clips"]]

    def edit(self, parent, clips, **kwargs):
        return editing_changes.create_task_edit(
            self.task_id, parent["revision_id"], clips, **kwargs)

    def resign(self, revision, filename, value):
        folder = self.folder_for(revision)
        (folder / filename).write_text(json.dumps(value), encoding="utf-8")
        record = json.loads((folder / "revision.json").read_text(encoding="utf-8"))
        record["files"][filename] = hashlib.sha256((folder / filename).read_bytes()).hexdigest()
        (folder / "revision.json").write_text(json.dumps(record), encoding="utf-8")

    def added_request(self):
        candidate = next(item for item in self.candidates["candidates"]
                         if item["id"] not in self.selected)
        return {"candidate_id": candidate["id"],
                "source_in_frame": seconds_to_frame(candidate["start"], self.source["fps"]),
                "source_out_frame": seconds_to_frame(candidate["end"], self.source["fps"])}

    def test_landscape_trim_reorder_preserves_baseline_media_and_previous_bytes(self):
        original = self.create()
        baseline = self.saved(original)
        before = {path.relative_to(self.folder): path.read_bytes()
                  for path in self.folder.rglob("*") if path.is_file()}
        before_job = deepcopy(jobs.get_job(self.task_id))
        requests = self.requests(baseline)
        requests[0]["source_in_frame"] += 2
        requests[0]["source_out_frame"] -= 3
        requests.reverse()
        with patch.object(editing, "build_media_manifest", side_effect=AssertionError("must not probe")):
            changed = self.edit(original, requests, editing_elapsed_ms=1234)
            saved = self.saved(changed)
        report = saved["edit_record"]
        self.assertEqual(report["baseline_revision_id"], original["revision_id"])
        self.assertEqual(report["parent_revision_id"], original["revision_id"])
        self.assertEqual(report["editing_elapsed_ms"], 1234)
        self.assertEqual(report["elapsed_time_scope"], "user_reported_session_elapsed_not_active_work")
        self.assertEqual(report["effort"]["net_boundary_adjustment_seconds_exact"], {"num": 1, "den": 6})
        self.assertEqual(report["effort"]["adjusted_clip_count"], 1)
        self.assertEqual(report["effort"]["minimum_moved_clip_count"], 1)
        self.assertEqual(saved["edl"]["clips"][0]["timeline_in_frame"], 0)
        provenance = {clip["id"]: clip["provenance"] for clip in baseline["edl"]["clips"]}
        self.assertEqual({clip["id"]: clip["provenance"] for clip in saved["edl"]["clips"]}, provenance)
        self.assertEqual(jobs.get_job(self.task_id), before_job)
        self.assertTrue(all((self.folder / path).read_bytes() == content for path, content in before.items()))
        self.assertEqual(list(self.folder.rglob("*.mp4")), [self.folder / "normalized.mp4"])
        self.assertEqual(editing.revision_source(self.task_id, changed["revision_id"]),
                         (self.folder / "normalized.mp4").resolve())

    def test_portrait_manual_edit_retains_profile_and_framing(self):
        original = self.create()
        portrait = editing.create_task_portrait(self.task_id, original["revision_id"])
        baseline = self.saved(portrait)
        requests = self.requests(baseline)
        requests[0]["source_in_frame"] += 1
        requests.reverse()
        changed = self.edit(portrait, requests)
        saved = self.saved(changed)
        self.assertEqual(saved["edl"]["schema_version"], "0.2.0")
        self.assertEqual(saved["edl"]["profile"], baseline["edl"]["profile"])
        self.assertEqual(saved["edl"]["framing"], baseline["edl"]["framing"])
        self.assertEqual(saved["edl"]["sources"], baseline["edl"]["sources"])
        self.assertEqual(saved["edit_record"]["effort"]["adjusted_clip_count"], 1)
        self.assertEqual(saved["edit_record"]["baseline_revision_id"], portrait["revision_id"])
        self.assertIsNone(saved["edit_record"]["editing_elapsed_ms"])

    def test_undo_has_zero_net_change_and_nonzero_parent_step(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        changed_requests = deepcopy(requests)
        changed_requests[0]["source_in_frame"] += 4
        changed = self.edit(original, changed_requests)
        undone = self.edit(changed, requests)
        record = self.saved(undone)["edit_record"]
        self.assertEqual(record["baseline_revision_id"], original["revision_id"])
        self.assertEqual(record["parent_revision_id"], changed["revision_id"])
        self.assertEqual(record["effort"]["adjusted_clip_count"], 0)
        self.assertEqual(record["effort"]["net_boundary_adjustment_seconds"], 0)
        self.assertEqual(record["step_effort"]["adjusted_clip_count"], 1)
        self.assertEqual(record["step_effort"]["net_boundary_adjustment_seconds_exact"], {"num": 2, "den": 15})

    def test_added_candidate_provenance_is_stable_across_order_delete_reintroduction(self):
        original = self.create()
        original_saved = self.saved(original)
        original_requests = self.requests(original_saved)
        added = self.added_request()
        requests = [added, *original_requests]
        changed = self.edit(original, requests)
        saved = self.saved(changed)
        added_provenance = saved["edl"]["clips"][0]["provenance"]
        original_positions = {clip["provenance"]["original_selection_index"]
                              for clip in original_saved["edl"]["clips"]}
        self.assertNotIn(added_provenance["original_selection_index"], original_positions)
        self.assertEqual(saved["edit_record"]["effort"]["added_clip_count"], 1)
        self.assertEqual(saved["edit_record"]["effort"]["minimum_moved_clip_count"], 0)
        removed = self.edit(changed, original_requests)
        restored = self.edit(removed, [*original_requests, added])
        restored_saved = self.saved(restored)
        self.assertEqual(restored_saved["edl"]["clips"][-1]["provenance"], added_provenance)
        self.assertEqual([clip["provenance"] for clip in restored_saved["edl"]["clips"][:-1]],
                         [clip["provenance"] for clip in original_saved["edl"]["clips"]])
        self.assertEqual(restored_saved["edit_record"]["baseline_revision_id"], original["revision_id"])

    def test_delete_does_not_create_false_boundary_or_reorder_changes(self):
        original = self.create()
        requests = self.requests(self.saved(original))[1:]
        changed = self.edit(original, requests)
        effort = self.saved(changed)["edit_record"]["effort"]
        self.assertEqual(effort["removed_clip_count"], 1)
        self.assertEqual(effort["adjusted_clip_count"], 0)
        self.assertEqual(effort["net_boundary_adjustment_seconds"], 0)
        self.assertEqual(effort["minimum_moved_clip_count"], 0)

    def test_saved_snapshot_does_not_depend_on_current_candidate_or_analysis_files(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        (self.folder / "candidates.json").write_text("current ranking unavailable", encoding="utf-8")
        (self.folder / "analysis.json").write_text("current analysis unavailable", encoding="utf-8")
        changed = self.edit(original, requests)
        saved = self.saved(changed)
        self.assertEqual([clip["provenance"]["candidate_id"] for clip in saved["edl"]["clips"]], self.selected)
        self.assertEqual(saved["edit_record"]["effort"]["net_boundary_adjustment_seconds"], 0)

    def test_available_candidates_come_from_saved_snapshot_not_current_files(self):
        original = self.create()
        saved = self.saved(original)
        expected = editing_changes.available_edit_clips(self.task_id, original["revision_id"], saved)
        (self.folder / "candidates.json").write_text("current ranking replaced", encoding="utf-8")
        (self.folder / "analysis.json").write_text("current analysis replaced", encoding="utf-8")
        with patch.object(editing, "build_media_manifest", side_effect=AssertionError("must not probe")):
            actual = editing_changes.available_edit_clips(self.task_id, original["revision_id"], saved)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["unavailable_edit_clip_count"], 0)
        self.assertEqual({clip["candidate_id"] for clip in actual["available_clips"]},
                         {candidate["id"] for candidate in self.candidates["candidates"]})

    def test_available_candidates_use_measured_fractional_fps_exact_frames(self):
        self.source["fps"] = {"num": 30000, "den": 1001}
        self.manifest["profile"]["fps"] = deepcopy(self.source["fps"])
        original = self.create()
        projection = editing_changes.available_edit_clips(
            self.task_id, original["revision_id"], self.saved(original))
        projected = {clip["candidate_id"]: (clip["source_in_frame"], clip["source_out_frame"])
                     for clip in projection["available_clips"]}
        expected_frames = {0: 0, 10: 300, 20: 599, 30: 899}
        for candidate in self.candidates["candidates"]:
            self.assertEqual(projected[candidate["id"]],
                             (expected_frames[candidate["start"]], expected_frames[candidate["end"]]))
        self.assertEqual(projection["unavailable_edit_clip_count"], 0)

    def test_unprojectable_saved_candidates_are_skipped_with_public_rejection_count(self):
        original = self.create()
        document = deepcopy(self.candidates)
        beyond_source = {**deepcopy(document["candidates"][0]),
                         "id": "outside-measured-source", "start": 30, "end": 31.2}
        empty_after_projection = {**deepcopy(document["candidates"][0]),
                                  "id": "zero-projected-frames", "start": 0.001, "end": 0.002}
        document["candidates"].extend([beyond_source, empty_after_projection])
        self.resign(original, "candidates.json", document)
        projection = editing_changes.available_edit_clips(
            self.task_id, original["revision_id"], self.saved(original))
        self.assertEqual(projection["unavailable_edit_clip_count"], 2)
        self.assertEqual({clip["candidate_id"] for clip in projection["available_clips"]},
                         {candidate["id"] for candidate in self.candidates["candidates"]})

    def test_sidecar_download_and_resigned_metric_tampering_fails_closed(self):
        original = self.create()
        changed = self.edit(original, self.requests(self.saved(original)))
        with editing.open_revision_file(self.task_id, changed["revision_id"], "edit-record.json") as incoming:
            record = json.load(incoming)
        self.assertEqual(record, self.saved(changed)["edit_record"])
        self.assertEqual(editing.revision_file(self.task_id, changed["revision_id"], "edit-record.json"),
                         self.folder_for(changed) / "edit-record.json")
        record["effort"]["adjusted_clip_count"] = 99
        self.resign(changed, "edit-record.json", record)
        with self.assertRaises(editing.ProjectError) as caught:
            self.saved(changed)
        self.assertEqual(caught.exception.status_code, 409)

    def test_missing_edit_record_is_not_a_readable_revision(self):
        original = self.create()
        changed = self.edit(original, self.requests(self.saved(original)))
        (self.folder_for(changed) / "edit-record.json").unlink()
        with self.assertRaises(editing.ProjectError):
            self.saved(changed)

    def test_valid_but_changed_portrait_profile_is_rejected_after_resigning_edl(self):
        original = self.create()
        portrait = editing.create_task_portrait(self.task_id, original["revision_id"])
        changed = self.edit(portrait, self.requests(self.saved(portrait)))
        project = deepcopy(self.saved(changed)["edl"])
        project["profile"].update(width=720, height=1280)
        self.resign(changed, "project.edl.json", project)
        with self.assertRaises(editing.ProjectError) as caught:
            self.saved(changed)
        self.assertEqual(caught.exception.status_code, 409)

    def test_edit_cannot_reset_adoption_baseline_to_hide_previous_changes(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        requests[0]["source_in_frame"] += 2
        changed = self.edit(original, requests)
        again = self.edit(changed, requests)
        folder = self.folder_for(again)
        revision = json.loads((folder / "revision.json").read_text(encoding="utf-8"))
        revision["derivation"]["baseline_revision_id"] = changed["revision_id"]
        revision["derivation"]["baseline_edl_file_sha256"] = changed["files"]["project.edl.json"]
        (folder / "revision.json").write_text(json.dumps(revision), encoding="utf-8")
        with self.assertRaises(editing.ProjectError) as caught:
            self.saved(again)
        self.assertEqual(caught.exception.status_code, 409)

    def test_cross_task_parent_reference_and_copied_revision_are_rejected(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        other_id = str(uuid4())
        other_folder = jobs.job_dir(other_id)
        other_folder.mkdir()
        jobs.create_job(other_id, "other.mp4", "source.mp4")
        jobs.claim_job(other_id)
        jobs.update_job(other_id, "SUCCEEDED", "done", 100, result=self.result)
        with self.assertRaises(editing.ProjectError) as caught:
            editing_changes.create_task_edit(other_id, original["revision_id"], requests)
        self.assertEqual(caught.exception.status_code, 404)
        copied = other_folder / "edits" / original["revision_id"]
        shutil.copytree(self.folder_for(original), copied)
        with self.assertRaises(editing.ProjectError) as caught:
            editing.load_task_edl(other_id, original["revision_id"])
        self.assertEqual(caught.exception.status_code, 409)

    def test_parent_self_cycle_and_excessive_ancestry_are_rejected(self):
        original = self.create()
        changed = self.edit(original, self.requests(self.saved(original)))
        folder = self.folder_for(changed)
        revision = json.loads((folder / "revision.json").read_text(encoding="utf-8"))
        revision["derivation"]["parent_revision_id"] = changed["revision_id"]
        revision["derivation"]["parent_edl_file_sha256"] = revision["files"]["project.edl.json"]
        (folder / "revision.json").write_text(json.dumps(revision), encoding="utf-8")
        with self.assertRaises(editing.ProjectError) as caught:
            self.saved(changed)
        self.assertEqual(caught.exception.status_code, 409)
        ancestors = frozenset(str(uuid4()) for _ in range(16))
        with self.assertRaises(editing.ProjectError) as caught:
            editing.load_task_edl(self.task_id, original["revision_id"], _visited=ancestors)
        self.assertEqual(caught.exception.status_code, 409)

    def test_depth_limit_prevents_edit_and_portrait_publication(self):
        original = self.create()
        saved = self.saved(original)
        saved["lineage_depth"] = 16
        before = list((self.folder / "edits").iterdir())
        with patch.object(editing, "load_task_edl", return_value=saved):
            with self.assertRaises(editing.ProjectError) as caught:
                self.edit(original, self.requests(saved))
            self.assertEqual(caught.exception.status_code, 422)
            with self.assertRaises(editing.ProjectError) as caught:
                editing.create_task_portrait(self.task_id, original["revision_id"])
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(list((self.folder / "edits").iterdir()), before)

    def test_invalid_frames_ids_elapsed_and_duplicate_requests_publish_nothing(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        invalid = [[], [*requests, requests[0]],
                   [{**requests[0], "candidate_id": "unknown-candidate"}],
                   [{**requests[0], "source_in_frame": True}],
                   [{**requests[0], "source_out_frame": 20.5}],
                   [{**requests[0], "source_in_frame": -1}],
                   [{**requests[0], "source_out_frame": self.source["total_frames"] + 1}],
                   [{**requests[0], "source_in_frame": requests[0]["source_out_frame"]}],
                   [{**requests[0], "extra": "not allowed"}]]
        before = list((self.folder / "edits").iterdir())
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(editing.ProjectError) as caught:
                self.edit(original, value)
            self.assertEqual(caught.exception.status_code, 422)
        for elapsed in (True, -1, 12.5, 86_400_001):
            with self.subTest(elapsed=elapsed), self.assertRaises(editing.ProjectError) as caught:
                self.edit(original, requests, editing_elapsed_ms=elapsed)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(list((self.folder / "edits").iterdir()), before)

    def test_partial_sidecar_write_failure_removes_unpublished_folder(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        before = {path.name for path in (self.folder / "edits").iterdir()}
        write = editing._write_json

        def fail_sidecar(path, value):
            if path.name == "edit-record.json":
                raise OSError("private disk diagnostic")
            return write(path, value)

        with patch.object(editing, "_write_json", side_effect=fail_sidecar):
            with self.assertRaises(editing.ProjectError) as caught:
                self.edit(original, requests)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private disk diagnostic", caught.exception.detail)
        self.assertEqual({path.name for path in (self.folder / "edits").iterdir()}, before)
        self.assertEqual(self.saved(original)["revision"], original)

    def test_portrait_of_edit_inherits_net_change_with_zero_automatic_step(self):
        original = self.create()
        requests = self.requests(self.saved(original))
        requests[0]["source_out_frame"] -= 6
        changed = self.edit(original, requests)
        portrait = editing.create_task_portrait(self.task_id, changed["revision_id"])
        saved = self.saved(portrait)
        record = saved["edit_record"]
        self.assertEqual(record["baseline_revision_id"], original["revision_id"])
        self.assertEqual(record["origin"], "automatic_center_crop_no_manual_operation")
        self.assertEqual(record["effort"]["net_boundary_adjustment_seconds_exact"], {"num": 1, "den": 5})
        self.assertEqual(record["step_effort"]["adjusted_clip_count"], 0)
        self.assertEqual(record["step_effort"]["net_boundary_adjustment_seconds"], 0)
        self.assertIsNone(record["editing_elapsed_ms"])
        later_requests = self.requests(saved)
        later_requests[0]["source_out_frame"] -= 3
        later = self.edit(portrait, later_requests)
        self.assertEqual(self.saved(later)["edit_record"]["baseline_revision_id"], original["revision_id"])
        self.assertEqual(self.saved(later)["edit_record"]["effort"]["net_boundary_adjustment_seconds_exact"],
                         {"num": 3, "den": 10})

    def test_legacy_original_and_portrait_revisions_remain_compatible(self):
        original = self.create()
        portrait = editing.create_task_portrait(self.task_id, original["revision_id"])
        for revision in (original, portrait):
            saved = self.saved(revision)
            self.assertEqual(set(saved["revision"]["files"]), editing.FILES)
            self.assertNotIn("edit_record", saved)
            with self.assertRaises(editing.ProjectError) as caught:
                editing.revision_file(self.task_id, revision["revision_id"], "edit-record.json")
            self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.saved(original)["lineage_depth"], 1)
        self.assertEqual(self.saved(portrait)["lineage_depth"], 2)


if __name__ == "__main__":
    unittest.main()
