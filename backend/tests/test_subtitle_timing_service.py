"""Timing-only child subtitles bind to an immutable original plan and evidence."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4
import xml.etree.ElementTree as ET

from fastapi import HTTPException
from pydantic import ValidationError

from backend.tests import test_editing_service as fixtures
from clipforge.editing.subtitles import retime_subtitles, to_ass
from clipforge.routes import editing as routes
from clipforge.services import editing_history, mlt_exporting, rendering, subtitling
from clipforge.storage import jobs


class SubtitleTimingServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.revision = fixtures.EditingServiceTests.create(self)
        self.revision_id = self.revision["revision_id"]
        self.version_folder = self.folder / "edits" / self.revision_id
        self.original = subtitling.create_task_subtitles(
            self.task_id, self.revision_id, font_name="Noto Sans CJK SC")
        self.subtitle_id = self.original["subtitle_id"]
        self.base = subtitling.load_task_subtitles(self.task_id, self.revision_id, self.subtitle_id)
        cue = next(cue for cue in self.base["plan"]["cues"]
                   if cue["timeline_out_frame"] - cue["timeline_in_frame"] > 2)
        self.edits = [{"cue_id": cue["id"], "timeline_in_frame": cue["timeline_in_frame"] + 1,
                       "timeline_out_frame": cue["timeline_out_frame"]}]
        self.evidence = "human-sync1-confirmed-start"

    def create(self, edits=None, evidence_id=None, parent_id=None):
        return subtitling.create_retimed_task_subtitles(
            self.task_id, self.revision_id, parent_id or self.subtitle_id,
            self.edits if edits is None else edits,
            evidence_id=self.evidence if evidence_id is None else evidence_id)

    def target(self, record):
        return self.version_folder / "subtitles" / record["subtitle_id"]

    def test_new_child_preserves_original_asr_plan_style_and_parent_files(self):
        before = {path.relative_to(self.folder): path.read_bytes()
                  for path in self.folder.rglob("*") if path.is_file()}
        job = jobs.get_job(self.task_id)
        child = self.create()
        loaded = subtitling.load_task_subtitles(self.task_id, self.revision_id, child["subtitle_id"])
        self.assertNotEqual(child["subtitle_id"], self.subtitle_id)
        self.assertEqual(child["format_version"], 2)
        self.assertEqual(child["font_name"], self.original["font_name"])
        self.assertEqual(child["timing_revision"], {
            "parent_subtitle_id": self.subtitle_id,
            "base_plan_sha256": self.original["plan_content_sha256"],
            "evidence_id": self.evidence, "edits": self.edits})
        self.assertEqual(loaded["plan"]["schema_version"], "0.2.0")
        self.assertEqual(loaded["plan"]["policy"], self.base["plan"]["policy"])
        for old, new in zip(self.base["plan"]["cues"], loaded["plan"]["cues"], strict=True):
            self.assertEqual({key: value for key, value in old.items()
                              if key not in ("timeline_in_frame", "timeline_out_frame")},
                             {key: value for key, value in new.items()
                              if key not in ("timeline_in_frame", "timeline_out_frame")})
        with subtitling.open_subtitle_inputs(self.task_id, self.revision_id, child["subtitle_id"]) as (held, project, analyses):
            self.assertEqual(held["plan"], retime_subtitles(
                self.base["plan"], project, analyses, self.edits, evidence_id=self.evidence))
        for filename in subtitling.SUBTITLE_FILES:
            with subtitling.open_subtitle_file(
                    self.task_id, self.revision_id, child["subtitle_id"], filename) as incoming:
                self.assertEqual(incoming.read(), (self.target(child) / filename).read_bytes())
        self.assertEqual(jobs.get_job(self.task_id), job)
        self.assertTrue(all((self.folder / path).read_bytes() == value for path, value in before.items()))
        self.assertEqual(subtitling.load_task_subtitles(
            self.task_id, self.revision_id, self.subtitle_id), self.base)

    def test_invalid_requests_are_rejected_without_publishing_a_folder(self):
        edit = self.edits[0]
        cases = [([], self.evidence), ([edit, edit], self.evidence),
                 ([{**edit, "timeline_in_frame": True}], self.evidence),
                 ([{**edit, "timeline_in_frame": 0.5}], self.evidence),
                 ([{**edit, "timeline_out_frame": edit["timeline_in_frame"]}], self.evidence),
                 ([{**edit, "cue_id": "absent"}], self.evidence),
                 ([{**edit, "timeline_out_frame": 999999}], self.evidence),
                 ([edit], " "), ([edit], " unsafe surrounding whitespace ")]
        for edits, evidence in cases:
            with self.subTest(edits=edits, evidence=evidence), self.assertRaises(subtitling.ProjectError) as caught:
                self.create(edits=edits, evidence_id=evidence)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual([path.name for path in self.target(self.original).parent.iterdir()], [self.subtitle_id])

    def test_already_retimed_parent_is_rejected_without_chaining(self):
        child = self.create()
        with self.assertRaises(subtitling.ProjectError) as caught:
            self.create(parent_id=child["subtitle_id"])
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(len(list(self.target(child).parent.iterdir())), 2)

    def test_metadata_parent_hash_evidence_font_downgrade_and_loop_tampering_rejected(self):
        child = self.create()
        path = self.target(child) / "subtitle-version.json"
        cases = [
            {**child, "format_version": 1},
            {**child, "font_name": "Another Font"},
            {**child, "timing_revision": {**child["timing_revision"], "parent_subtitle_id": child["subtitle_id"]}},
            {**child, "timing_revision": {**child["timing_revision"], "parent_subtitle_id": str(uuid4())}},
            {**child, "timing_revision": {**child["timing_revision"], "base_plan_sha256": "0" * 64}},
            {**child, "timing_revision": {**child["timing_revision"], "evidence_id": "different"}},
            {**child, "timing_revision": {**child["timing_revision"], "unexpected": True}},
        ]
        for value in cases:
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(subtitling.ProjectError) as caught:
                subtitling.load_task_subtitles(self.task_id, self.revision_id, child["subtitle_id"])
            self.assertEqual(caught.exception.status_code, 409)
        path.write_text(json.dumps(child), encoding="utf-8")
        subtitling.load_task_subtitles(self.task_id, self.revision_id, child["subtitle_id"])

    def test_rehashed_plan_text_tampering_and_changed_parent_are_rejected(self):
        child = self.create()
        path = self.target(child) / "subtitles.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["cues"][0]["text"] += "tampered"
        path.write_text(json.dumps(plan), encoding="utf-8")
        metadata = deepcopy(child)
        metadata["files"]["subtitles.json"] = subtitling._digest(path)
        metadata["plan_content_sha256"] = subtitling._content_hash(plan)
        (self.target(child) / "subtitle-version.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(subtitling.ProjectError) as caught:
            subtitling.load_task_subtitles(self.task_id, self.revision_id, child["subtitle_id"])
        self.assertEqual(caught.exception.status_code, 409)
        independent = self.create()
        (self.target(self.original) / "captions.srt").write_text("changed original", encoding="utf-8")
        with self.assertRaises(subtitling.ProjectError) as caught:
            subtitling.load_task_subtitles(self.task_id, self.revision_id, independent["subtitle_id"])
        self.assertEqual(caught.exception.status_code, 409)

    def test_failed_publication_and_uuid_collision_preserve_existing_versions(self):
        before = {path.name: path.read_bytes() for path in self.target(self.original).iterdir()}
        with patch.object(subtitling, "uuid4", return_value=self.subtitle_id):
            with self.assertRaises(subtitling.ProjectError) as caught:
                self.create()
        self.assertEqual(caught.exception.status_code, 503)
        with patch.object(subtitling, "_write_text", side_effect=OSError("private details")):
            with self.assertRaises(subtitling.ProjectError) as caught:
                self.create()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private", caught.exception.detail)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.target(self.original).iterdir()})
        self.assertEqual([path.name for path in self.target(self.original).parent.iterdir()], [self.subtitle_id])

    def test_timing_child_remains_discoverable_and_bound_through_mlt_and_render_services(self):
        child = self.create()
        history = editing_history.list_revision_artifacts(self.task_id, self.revision_id)
        item = next(item for item in history["subtitles"] if item["subtitle_id"] == child["subtitle_id"])
        self.assertEqual(item["timing_revision"], child["timing_revision"])
        exported = mlt_exporting.create_task_mlt_export(
            self.task_id, self.revision_id, subtitle_id=child["subtitle_id"])
        self.assertEqual(mlt_exporting.load_task_mlt_export(
            self.task_id, self.revision_id, exported["export_id"])["export"], exported)
        with mlt_exporting.open_mlt_export_file(
                self.task_id, self.revision_id, exported["export_id"], "project.mlt") as incoming:
            root = ET.fromstring(incoming.read())
        loaded = subtitling.load_task_subtitles(self.task_id, self.revision_id, child["subtitle_id"])
        cue = next(cue for cue in loaded["plan"]["cues"] if cue["id"] == self.edits[0]["cue_id"])
        effects = root.findall("tractor/filter")
        self.assertTrue(any(effect.get("in") == str(cue["timeline_in_frame"])
                            and effect.get("out") == str(cue["timeline_out_frame"] - 1)
                            for effect in effects))

        def fake_render(project, source_root, output, **options):
            plan = options["subtitle_plan"]
            self.assertEqual(plan, loaded["plan"])
            self.assertEqual(plan["schema_version"], "0.2.0")
            Path(output).write_bytes(b"stub MP4: corrected subtitle binding only")
            ass = to_ass(plan, font_name=child["font_name"])
            return {"artifact_kind": "edl_render_verification", "edl_sha256": rendering._content_hash(project),
                    "output": {"path": str(output), "sha256": rendering._digest(output),
                               "total_frames": project["duration_frames"], "profile": project["profile"]},
                    "subtitles": {"plan_sha256": rendering._content_hash(plan),
                                  "font_name": child["font_name"],
                                  "ass_sha256": hashlib.sha256(ass.encode("utf-8")).hexdigest()}}

        with patch("clipforge.editing.render.render_edl", side_effect=fake_render):
            rendered = rendering.create_task_render(
                self.task_id, self.revision_id, subtitle_id=child["subtitle_id"])
        result = rendering.load_task_render(self.task_id, self.revision_id, rendered["render_id"])
        self.assertEqual(result["render"]["subtitle_id"], child["subtitle_id"])
        self.assertEqual(result["render"]["subtitle_plan_sha256"], child["plan_content_sha256"])


class SubtitleTimingRouteTests(unittest.TestCase):
    def test_route_uses_explicit_frames_evidence_and_new_immutable_location(self):
        task, revision, parent, child = [UUID(str(uuid4())) for _ in range(4)]
        payload = routes.SubtitleTimingRequest(evidence_id="human-sync1", edits=[{
            "cue_id": "cue_0000_0000", "timeline_in_frame": 135, "timeline_out_frame": 200}])
        record = {"task_id": str(task), "revision_id": str(revision), "subtitle_id": str(child)}
        with patch.object(routes.subtitling_service, "create_retimed_task_subtitles", return_value=record) as create:
            response = routes.create_retimed_subtitles(task, revision, parent, payload)
        create.assert_called_once_with(str(task), str(revision), str(parent), [payload.edits[0].model_dump()],
                                       evidence_id="human-sync1")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.body), record)
        self.assertEqual(response.headers["location"], f"/tasks/{task}/edl/{revision}/subtitles/{child}")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_route_request_rejects_coercion_unknown_fields_duplicates_and_empty_evidence(self):
        edit = {"cue_id": "cue", "timeline_in_frame": 10, "timeline_out_frame": 20}
        for payload in ({"evidence_id": "human", "edits": []}, {"evidence_id": " ", "edits": [edit]},
                        {"evidence_id": "human", "edits": [edit, edit]},
                        {"evidence_id": "human", "edits": [{**edit, "timeline_in_frame": "10"}]},
                        {"evidence_id": "human", "edits": [{**edit, "timeline_in_frame": True}]},
                        {"evidence_id": "human", "edits": [{**edit, "unexpected": 1}]},
                        {"evidence_id": "human", "edits": [edit], "font_name": "unrequested"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                routes.SubtitleTimingRequest.model_validate(payload)

    def test_route_preserves_deliberately_safe_service_error(self):
        task = UUID(str(uuid4()))
        payload = routes.SubtitleTimingRequest(evidence_id="human", edits=[{
            "cue_id": "cue", "timeline_in_frame": 10, "timeline_out_frame": 20}])
        with patch.object(routes.subtitling_service, "create_retimed_task_subtitles",
                          side_effect=subtitling.ProjectError(422, "safe timing error")):
            with self.assertRaises(HTTPException) as caught:
                routes.create_retimed_subtitles(task, task, task, payload)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail, "safe timing error")


if __name__ == "__main__":
    unittest.main()
