"""Manual changes capture supplied names and exact differences without schema changes."""

from copy import deepcopy
import unittest

from backend.tests import test_editing_changes as changes_fixtures
from clipforge.services import editing, reviewing


class EditingActorAuditTests(unittest.TestCase):
    setUp = changes_fixtures.EditingChangesServiceTests.setUp
    create = changes_fixtures.EditingChangesServiceTests.create
    saved = changes_fixtures.EditingChangesServiceTests.saved
    folder_for = changes_fixtures.EditingChangesServiceTests.folder_for
    requests = changes_fixtures.EditingChangesServiceTests.requests
    edit = changes_fixtures.EditingChangesServiceTests.edit
    resign = changes_fixtures.EditingChangesServiceTests.resign
    added_request = changes_fixtures.EditingChangesServiceTests.added_request
    def test_named_edits_record_boundaries_order_and_additions_in_immutable_sidecar(self):
        original = self.create()
        baseline = self.saved(original)
        clips = self.requests(baseline)
        clips[0]["source_in_frame"] += 2
        clips.reverse()
        clips.append(self.added_request())
        revision = self.edit(original, clips, actor="  张同学  ")
        saved = self.saved(revision)
        record = saved["edit_record"]
        self.assertEqual(record["format_version"], 2)
        self.assertEqual(record["actor"], "张同学")
        self.assertEqual(record["actor_scope"], "user_supplied_name")
        self.assertEqual(record["created_at"], revision["created_at"])
        self.assertEqual(record["candidate_snapshot_sha256"], original["files"]["candidates.json"])
        self.assertEqual(record["before"], reviewing.clip_state(baseline["edl"]))
        self.assertEqual(record["after"], reviewing.clip_state(saved["edl"]))
        self.assertEqual({item["kind"] for item in record["operations"]}, {"boundary_changed", "added", "reordered"})
        self.assertEqual(saved["edl"]["schema_version"], baseline["edl"]["schema_version"])
        self.assertEqual(set(saved["edl"]), set(baseline["edl"]))
        audit = reviewing.task_audit(self.task_id)
        manual = [event for event in audit["events"] if event["kind"] == "manual_edit"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["operations"], record["operations"])
        self.assertEqual(audit["unrecorded_actor_edit_count"], 0)
        removed = self.edit(revision, clips[:-1], actor="张同学")
        operations = self.saved(removed)["edit_record"]["operations"]
        self.assertEqual([item["kind"] for item in operations], ["removed"])
        self.assertEqual(operations[0]["before"], record["after"]["clips"][-1])
        self.assertIsNone(operations[0]["after"])

    def test_legacy_requests_and_format_one_records_remain_unrecorded(self):
        original = self.create()
        revision = self.edit(original, self.requests(self.saved(original)))
        record = self.saved(revision)["edit_record"]
        self.assertIsNone(record["actor"])
        self.assertEqual(record["actor_scope"], "unrecorded")
        legacy = deepcopy(record)
        legacy["format_version"] = 1
        for name in ("actor", "actor_scope", "created_at", "candidate_snapshot_sha256", "before", "after", "operations"):
            legacy.pop(name)
        self.resign(revision, "edit-record.json", legacy)
        self.assertEqual(self.saved(revision)["edit_record"], legacy)
        audit = reviewing.task_audit(self.task_id)
        self.assertEqual(audit["unrecorded_actor_edit_count"], 1)
        self.assertTrue(all(event["actor"] is None for event in audit["events"]))
        self.assertTrue(all(event["actor_scope"] == "unrecorded" for event in audit["events"]))

    def test_initial_selection_records_operator_order_and_legacy_unknown_identity(self):
        from unittest.mock import patch
        with patch.object(editing, "build_media_manifest", return_value=self.manifest):
            revision = editing.create_task_edl(self.task_id, self.selected, actor="  王同学 ")
        saved = self.saved(revision)
        self.assertEqual(saved["revision"]["actor"], "王同学")
        event = reviewing.task_audit(self.task_id)["events"][0]
        self.assertEqual(event["kind"], "edl_adoption")
        self.assertEqual(event["actor"], "王同学")
        self.assertEqual(event["before"], {"duration_frames": 0, "clips": []})
        self.assertEqual([clip["candidate_id"] for clip in event["after"]["clips"]], self.selected)
        folder = self.folder_for(revision)
        old = deepcopy(revision)
        old.pop("actor")
        old.pop("actor_scope")
        import json
        (folder / "revision.json").write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(self.saved(revision)["revision"], old)
        legacy = reviewing.task_audit(self.task_id)
        self.assertIsNone(legacy["events"][0]["actor"])
        self.assertEqual(legacy["unrecorded_actor_event_count"], 1)

    def test_invalid_names_publish_nothing_and_rewritten_evidence_fails_validation(self):
        original = self.create()
        clips = self.requests(self.saved(original))
        before = set(path.name for path in (self.folder / "edits").iterdir())
        for actor in (" ", "x" * 81, False, "张\n同学"):
            with self.subTest(actor=actor), self.assertRaises(editing.ProjectError) as caught:
                self.edit(original, clips, actor=actor)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(set(path.name for path in (self.folder / "edits").iterdir()), before)
        revision = self.edit(original, clips, actor="张同学")
        record = self.saved(revision)["edit_record"]
        record["before"]["clips"][0]["source_in_frame"] += 1
        self.resign(revision, "edit-record.json", record)
        with self.assertRaises(editing.ProjectError) as caught:
            self.saved(revision)
        self.assertEqual(caught.exception.status_code, 409)

    def test_audit_orders_real_instants_and_rejects_invalid_legacy_time(self):
        import json
        first, second = self.create(), self.create()
        first["created_at"] = "2026-10-01T17:00:00+08:00"
        second["created_at"] = "2026-10-01T08:00:00+00:00"
        for revision in (first, second):
            (self.folder_for(revision) / "revision.json").write_text(json.dumps(revision), encoding="utf-8")
        audit = reviewing.task_audit(self.task_id)
        self.assertEqual([event["revision_id"] for event in audit["events"]], [second["revision_id"], first["revision_id"]])
        first["created_at"] = "invalid timestamp"
        (self.folder_for(first) / "revision.json").write_text(json.dumps(first), encoding="utf-8")
        with self.assertRaises(editing.ProjectError) as caught:
            reviewing.task_audit(self.task_id)
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
