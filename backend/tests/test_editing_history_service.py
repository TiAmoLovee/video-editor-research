"""History discovery uses real saved readers without publication or media tools."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests import test_editing_service as fixtures
from backend.tests import test_rendering_service as render_fixtures
from clipforge.services import editing, editing_history, mlt_exporting, subtitling
from clipforge.storage import jobs


class EditingHistoryServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)

    def create(self, selected=None):
        return fixtures.EditingServiceTests.create(self, selected=selected)

    def folder_for(self, record):
        return self.folder / "edits" / record["revision_id"]

    def timestamp(self, record, value):
        path = self.folder_for(record) / "revision.json"
        updated = {**record, "created_at": value}
        path.write_text(json.dumps(updated), encoding="utf-8")
        return updated

    def test_empty_completed_task_does_not_create_history_directory(self):
        before = jobs.get_job(self.task_id)
        result = editing_history.list_task_edls(self.task_id)
        self.assertEqual(result, {"items": [], "total": 0, "offset": 0, "limit": 10,
                                  "has_more": False, "unavailable_count": 0})
        self.assertFalse((self.folder / "edits").exists())
        self.assertEqual(jobs.get_job(self.task_id), before)

    def test_exact_selection_profiles_sort_and_pagination_are_stable(self):
        first = self.timestamp(self.create(), "2026-10-01T08:00:00+00:00")
        second = self.timestamp(self.create(selected=list(reversed(self.selected))), "2026-10-02T08:00:00+00:00")
        portrait = self.timestamp(editing.create_task_portrait(self.task_id, first["revision_id"]),
                                  "2026-10-02T16:00:00+08:00")
        expected = sorted([second, portrait], key=lambda record: record["revision_id"], reverse=True) + [first]
        complete = editing_history.list_task_edls(self.task_id)
        self.assertEqual([item["revision_id"] for item in complete["items"]],
                         [record["revision_id"] for record in expected])
        for item in complete["items"]:
            saved = editing.load_task_edl(self.task_id, item["revision_id"])
            self.assertEqual(item["profile"], saved["edl"]["profile"])
            self.assertEqual(item["selected_ids"],
                             [clip["provenance"]["candidate_id"] for clip in saved["edl"]["clips"]])
        first_page = editing_history.list_task_edls(self.task_id, limit=1)
        self.assertEqual(first_page["items"], complete["items"][:1])
        self.assertEqual(first_page["total"], 3)
        self.assertTrue(first_page["has_more"])
        last_page = editing_history.list_task_edls(self.task_id, limit=1, offset=2)
        self.assertEqual(last_page["items"], complete["items"][2:])
        self.assertFalse(last_page["has_more"])
        self.assertEqual(editing_history.list_task_edls(self.task_id, offset=3)["items"], [])

    def test_corrupt_completed_records_count_but_unfinished_and_invalid_names_are_ignored(self):
        good, corrupt, missing, invalid_date = self.create(), self.create(), self.create(), self.create()
        (self.folder_for(corrupt) / "project.edl.json").write_text("tampered", encoding="utf-8")
        (self.folder_for(missing) / "revision.json").unlink()
        self.timestamp(invalid_date, "not an aware timestamp")
        wrong = self.folder / "edits" / "not-a-uuid"
        wrong.mkdir()
        (wrong / "revision.json").write_text("{}", encoding="utf-8")
        incomplete = self.folder / "edits" / str(uuid4())
        incomplete.mkdir()
        (incomplete / "project.edl.json").write_text("partial", encoding="utf-8")
        result = editing_history.list_task_edls(self.task_id)
        self.assertEqual([item["revision_id"] for item in result["items"]], [good["revision_id"]])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["unavailable_count"], 2)
        self.assertNotIn(str(self.folder), json.dumps(result))

    def test_history_uses_snapshots_without_reprobing_or_writing(self):
        revision = self.create()
        (self.folder / "candidates.json").write_text("current rankings unavailable", encoding="utf-8")
        before = {path.relative_to(self.folder).as_posix(): path.read_bytes()
                  for path in self.folder.rglob("*") if path.is_file()}
        before_job = deepcopy(jobs.get_job(self.task_id))
        with patch.object(editing, "build_media_manifest", side_effect=AssertionError("must not probe")), \
                patch.object(editing, "_write_json", side_effect=AssertionError("must not publish")):
            result = editing_history.list_task_edls(self.task_id)
            artifacts = editing_history.list_revision_artifacts(self.task_id, revision["revision_id"])
        self.assertEqual(result["items"][0]["selected_ids"], self.selected)
        self.assertEqual(artifacts, {"subtitles": [], "renders": [], "mlt_exports": [], "unavailable_count": 0})
        self.assertEqual({path.relative_to(self.folder).as_posix(): path.read_bytes()
                          for path in self.folder.rglob("*") if path.is_file()}, before)
        self.assertEqual(jobs.get_job(self.task_id), before_job)

    def test_cross_task_record_is_not_returned(self):
        record = self.create()
        other_id = str(uuid4())
        other = jobs.job_dir(other_id)
        other.mkdir()
        jobs.create_job(other_id, "other.mp4", "source.mp4")
        jobs.claim_job(other_id)
        jobs.update_job(other_id, "SUCCEEDED", "done", 100, result={})
        target = other / "edits" / record["revision_id"]
        shutil.copytree(self.folder_for(record), target)
        result = editing_history.list_task_edls(other_id)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unavailable_count"], 1)
        with self.assertRaises(editing.ProjectError):
            editing_history.list_revision_artifacts(other_id, record["revision_id"])

    def test_unrecognized_metadata_fields_never_expose_paths(self):
        revision = self.create()
        path = self.folder_for(revision) / "revision.json"
        path.write_text(json.dumps({**revision, "private_path": str(self.folder)}), encoding="utf-8")
        subtitle = subtitling.create_task_subtitles(self.task_id, revision["revision_id"])
        marker = self.folder_for(revision) / "subtitles" / subtitle["subtitle_id"] / "subtitle-version.json"
        marker.write_text(json.dumps({**subtitle, "private_path": str(self.folder)}), encoding="utf-8")
        self.assertNotIn("private_path", editing_history.list_task_edls(self.task_id)["items"][0])
        artifacts = editing_history.list_revision_artifacts(self.task_id, revision["revision_id"])
        self.assertEqual(artifacts["subtitles"], [subtitle])
        self.assertNotIn(str(self.folder), json.dumps(artifacts))

    def test_unsafe_child_is_never_read_and_unsafe_parent_is_rejected(self):
        record = self.create()
        unsafe = self.folder_for(record)
        actual_resolve = Path.resolve
        def redirected(path, *args, **kwargs):
            if path == unsafe:
                return unsafe.parent / "outside"
            return actual_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", redirected), patch.object(editing_history, "load_task_edl") as load:
            result = editing_history.list_task_edls(self.task_id)
        load.assert_not_called()
        self.assertEqual(result["unavailable_count"], 1)
        parent = self.folder / "edits"
        def unsafe_parent(path, *args, **kwargs):
            return parent.parent / "outside" if path == parent else actual_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", unsafe_parent):
            with self.assertRaises(editing.ProjectError) as caught:
                editing_history.list_task_edls(self.task_id)
        self.assertEqual(caught.exception.status_code, 409)

    def test_completion_symlink_is_not_followed(self):
        record = self.create()
        marker = self.folder_for(record) / "revision.json"
        actual = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == marker or actual(path)), \
                patch.object(editing_history, "load_task_edl") as load:
            result = editing_history.list_task_edls(self.task_id)
        load.assert_not_called()
        self.assertEqual(result["items"], [])
        self.assertEqual(result["unavailable_count"], 1)

    def test_invalid_pagination_unknown_task_and_unfinished_task_are_safe(self):
        for limit, offset in ((0, 0), (51, 0), (True, 0), (10, -1), (10, 501), (10, False)):
            with self.subTest(limit=limit, offset=offset), self.assertRaises(editing.ProjectError) as caught:
                editing_history.list_task_edls(self.task_id, limit=limit, offset=offset)
            self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(editing.ProjectError) as caught:
            editing_history.list_task_edls(str(uuid4()))
        self.assertEqual(caught.exception.status_code, 404)
        pending = str(uuid4())
        jobs.create_job(pending, "pending.mp4", "source.mp4")
        with self.assertRaises(editing.ProjectError) as caught:
            editing_history.list_task_edls(pending)
        self.assertEqual(caught.exception.status_code, 409)

    def test_record_and_scan_budgets_return_safe_error_without_partial_page(self):
        self.create()
        with patch.object(editing_history, "MAX_COMPLETED_RECORDS", 0):
            with self.assertRaises(editing.ProjectError) as caught:
                editing_history.list_task_edls(self.task_id)
        self.assertEqual(caught.exception.status_code, 503)
        with patch.object(editing_history, "MAX_DIRECTORY_ENTRIES", 0):
            with self.assertRaises(editing.ProjectError) as caught:
                editing_history.list_task_edls(self.task_id)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn(str(self.folder), caught.exception.detail)

    def test_verified_artifact_history_and_tamper_detection(self):
        self.revision = self.create()
        self.revision_id = self.revision["revision_id"]
        self.revision_folder = self.folder_for(self.revision)
        self.project = editing.load_task_edl(self.task_id, self.revision_id)["edl"]
        subtitle = subtitling.create_task_subtitles(self.task_id, self.revision_id, font_name="Microsoft YaHei")
        render = render_fixtures.RenderingServiceTests.run_render(self)
        plain_mlt = mlt_exporting.create_task_mlt_export(self.task_id, self.revision_id)
        subtitled_mlt = mlt_exporting.create_task_mlt_export(self.task_id, self.revision_id,
                                                          subtitle_id=subtitle["subtitle_id"])
        before = {path.relative_to(self.folder).as_posix(): path.read_bytes()
                  for path in self.folder.rglob("*") if path.is_file()}
        result = editing_history.list_revision_artifacts(self.task_id, self.revision_id)
        self.assertEqual(result["subtitles"], [subtitle])
        self.assertEqual(result["renders"], [render])
        self.assertEqual({item["export_id"] for item in result["mlt_exports"]},
                         {plain_mlt["export_id"], subtitled_mlt["export_id"]})
        self.assertEqual(result["unavailable_count"], 0)
        self.assertEqual({path.relative_to(self.folder).as_posix(): path.read_bytes()
                          for path in self.folder.rglob("*") if path.is_file()}, before)
        (self.revision_folder / "renders" / render["render_id"] / "output.mp4").write_bytes(b"modified MP4")
        (self.revision_folder / "subtitles" / subtitle["subtitle_id"] / "captions.srt").write_text("changed", encoding="utf-8")
        partial = self.revision_folder / "renders" / str(uuid4())
        partial.mkdir()
        (partial / "output.mp4").write_bytes(b"incomplete")
        result = editing_history.list_revision_artifacts(self.task_id, self.revision_id)
        self.assertEqual(result["subtitles"], [])
        self.assertEqual(result["renders"], [])
        self.assertEqual(result["mlt_exports"], [plain_mlt])
        self.assertEqual(result["unavailable_count"], 3)
        self.assertNotIn(str(self.folder), json.dumps(result))

    def test_artifact_record_cannot_move_between_revisions(self):
        first, second = self.create(), self.create()
        subtitle = subtitling.create_task_subtitles(self.task_id, first["revision_id"])
        source = self.folder_for(first) / "subtitles" / subtitle["subtitle_id"]
        dest = self.folder_for(second) / "subtitles" / subtitle["subtitle_id"]
        shutil.copytree(source, dest)
        result = editing_history.list_revision_artifacts(self.task_id, second["revision_id"])
        self.assertEqual(result["subtitles"], [])
        self.assertEqual(result["unavailable_count"], 1)
        self.assertEqual(editing_history.list_revision_artifacts(self.task_id, first["revision_id"])["subtitles"],
                         [subtitle])


if __name__ == "__main__":
    unittest.main()
