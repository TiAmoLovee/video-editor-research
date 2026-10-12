"""Subtitle versions preserve original results and bind to saved EDL/analysis."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.tests import test_editing_service as fixtures
from clipforge.editing.subtitles import to_ass
from clipforge.services import subtitling, rendering
from clipforge.storage import jobs


class SubtitlingServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.revision = fixtures.EditingServiceTests.create(self)
        self.revision_id = self.revision["revision_id"]
        self.revision_folder = self.folder / "edits" / self.revision_id
        self.project = json.loads((self.revision_folder / "project.edl.json").read_text(encoding="utf-8"))

    def create(self):
        return subtitling.create_task_subtitles(self.task_id, self.revision_id, font_name="Microsoft YaHei")

    def test_uses_saved_analysis_and_keeps_legacy_result_and_revision(self):
        before = jobs.get_job(self.task_id)
        original = {p.name: p.read_bytes() for p in self.revision_folder.iterdir() if p.is_file()}
        (self.folder / "analysis.json").write_text("new current analysis unavailable", encoding="utf-8")
        record = self.create()
        saved = subtitling.load_task_subtitles(self.task_id, self.revision_id, record["subtitle_id"])
        self.assertEqual(saved["subtitle"], record)
        self.assertGreater(record["cue_count"], 0)
        self.assertEqual(record["analysis_file_sha256"], self.revision["files"]["analysis.json"])
        self.assertTrue(all((self.revision_folder / name).read_bytes() == value for name, value in original.items()))
        self.assertEqual(jobs.get_job(self.task_id), before)
        self.assertEqual((self.folder / "result.zip").read_bytes(), b"old ZIP unchanged")
        for name in subtitling.SUBTITLE_FILES:
            with subtitling.open_subtitle_file(self.task_id, self.revision_id, record["subtitle_id"], name) as stream:
                self.assertTrue(stream.read())
            self.assertTrue(stream.closed)

    def test_versions_are_independent_and_completion_record_required(self):
        first, second = self.create(), self.create()
        self.assertNotEqual(first["subtitle_id"], second["subtitle_id"])
        folder = self.revision_folder / "subtitles" / first["subtitle_id"]
        (folder / "subtitle-version.json").unlink()
        with self.assertRaises(subtitling.ProjectError) as caught:
            subtitling.load_task_subtitles(self.task_id, self.revision_id, first["subtitle_id"])
        self.assertEqual(caught.exception.status_code, 404)
        subtitling.load_task_subtitles(self.task_id, self.revision_id, second["subtitle_id"])

    def test_tampered_files_and_identity_rejected(self):
        for filename in subtitling.SUBTITLE_FILES:
            record = self.create()
            folder = self.revision_folder / "subtitles" / record["subtitle_id"]
            (folder / filename).write_bytes(b"changed")
            with self.subTest(filename=filename), self.assertRaises(subtitling.ProjectError):
                subtitling.load_task_subtitles(self.task_id, self.revision_id, record["subtitle_id"])
        record = self.create()
        folder = self.revision_folder / "subtitles" / record["subtitle_id"]
        for change in ({"cue_count": True}, {"task_id": "other"}, {"font_name": "Other Font"},
                       {"analysis_file_sha256": "0" * 64}, {"plan_content_sha256": "0" * 64}):
            value = {**record, **change}
            (folder / "subtitle-version.json").write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(subtitling.ProjectError):
                subtitling.load_task_subtitles(self.task_id, self.revision_id, record["subtitle_id"])

    def test_download_allowlist_and_changed_original_analysis(self):
        record = self.create()
        with self.assertRaises(subtitling.ProjectError):
            with subtitling.open_subtitle_file(self.task_id, self.revision_id, record["subtitle_id"], "../analysis.json"):
                self.fail("must reject traversal")
        (self.revision_folder / "analysis.json").write_text("modified", encoding="utf-8")
        with self.assertRaises(subtitling.ProjectError):
            subtitling.load_task_subtitles(self.task_id, self.revision_id, record["subtitle_id"])

    def test_save_failure_and_uuid_collision_do_not_delete_an_existing_version(self):
        record = self.create()
        prior = self.revision_folder / "subtitles" / record["subtitle_id"]
        originals = {p.name: p.read_bytes() for p in prior.iterdir()}
        with patch.object(subtitling, "uuid4", return_value=record["subtitle_id"]):
            with self.assertRaises(subtitling.ProjectError):
                self.create()
        self.assertTrue(all((prior / n).read_bytes() == v for n, v in originals.items()))
        with patch.object(subtitling, "_write_json", side_effect=OSError("private disk details")):
            with self.assertRaises(subtitling.ProjectError) as caught:
                self.create()
        self.assertNotIn("private", caught.exception.detail)
        self.assertEqual([p.name for p in prior.parent.iterdir()], [record["subtitle_id"]])

    def render(self, subtitle_id, bad_report=False):
        def fake_render(project, source_root, output, **options):
            self.assertEqual(project, self.project)
            plan = options["subtitle_plan"]
            self.assertEqual(len(options["analyses"]), 1)
            self.assertEqual(options["font_file"], "configured font")
            Path(output).write_bytes(b"stub subtitle MP4; actual content separately verified")
            return {"artifact_kind": "edl_render_verification", "edl_sha256": rendering._content_hash(project),
                    "output": {"path": str(output), "sha256": rendering._digest(output),
                               "total_frames": project["duration_frames"], "profile": project["profile"]},
                    "subtitles": {"plan_sha256": "f" * 64 if bad_report else rendering._content_hash(plan),
                                  "font_name": "Microsoft YaHei",
                                  "ass_sha256": hashlib.sha256(
                                      to_ass(plan, font_name="Microsoft YaHei").encode("utf-8")).hexdigest()}}
        with patch("clipforge.editing.render.render_edl", side_effect=fake_render):
            return rendering.create_task_render(self.task_id, self.revision_id, subtitle_id=subtitle_id,
                                                font_file="configured font", font_name="Microsoft YaHei")

    def test_render_uses_and_snapshots_saved_subtitle_version(self):
        subtitle = self.create()
        record = self.render(subtitle["subtitle_id"])
        self.assertEqual(record["subtitle_id"], subtitle["subtitle_id"])
        self.assertEqual(set(record["files"]), rendering.SUBTITLED_RENDER_FILES)
        saved = rendering.load_task_render(self.task_id, self.revision_id, record["render_id"])
        self.assertEqual(saved["render"], record)
        for name in ("captions.srt", "captions.ass", "subtitles.json"):
            with rendering.open_render_file(self.task_id, self.revision_id, record["render_id"], name) as stream:
                self.assertTrue(stream.read())
        folder = self.revision_folder / "renders" / record["render_id"]
        plan = json.loads((folder / "subtitles.json").read_text(encoding="utf-8"))
        altered = deepcopy(plan)
        altered["cues"][0]["timeline_in_frame"] += 1
        (folder / "subtitles.json").write_text(json.dumps(altered), encoding="utf-8")
        metadata = deepcopy(record)
        metadata["files"]["subtitles.json"] = rendering._digest(folder / "subtitles.json")
        (folder / "render.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(rendering.ProjectError):
            rendering.load_task_render(self.task_id, self.revision_id, record["render_id"])

    def test_render_rejects_bad_subtitle_report_and_preserves_old_plain_render(self):
        subtitle = self.create()
        with self.assertRaises(rendering.ProjectError):
            self.render(subtitle["subtitle_id"], bad_report=True)
        self.assertEqual(list((self.revision_folder / "renders").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
