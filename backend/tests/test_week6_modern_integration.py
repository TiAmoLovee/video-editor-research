"""Week 6 review/audit/ZIP support preserves whole-response and fitted EDLs."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4
from zipfile import ZIP_STORED, ZipFile

from backend.tests import test_task_response_candidates as response_fixtures
from clipforge.editing.render import _filter_graph
from clipforge.routes import editing as routes
from clipforge.services import batch_export, editing, editing_changes, reviewing


class Week6ModernIntegrationTests(unittest.TestCase):
    setUp = response_fixtures.TaskResponseTests.setUp
    write = staticmethod(response_fixtures.TaskResponseTests.write)
    preview = response_fixtures.TaskResponseTests.preview
    adopt = response_fixtures.TaskResponseTests.adopt

    def render_stub(self, project, source_root, output, **options):
        self.assertEqual(source_root, self.folder.resolve())
        self.assertEqual(project["schema_version"], "0.3.0")
        Path(output).write_bytes(f"fitted clip {project['clips'][0]['source_in_frame']}".encode())
        return {"artifact_kind": "edl_render_verification", "edl_sha256": batch_export._content_hash(project),
                "output": {"path": str(output), "sha256": editing._digest(output),
                           "total_frames": project["duration_frames"], "profile": project["profile"]},
                "clips": _filter_graph(project)[2], "framing": deepcopy(project["framing"])}

    def fitted(self):
        original = self.adopt(actor="集成验收")
        fitted = editing.create_task_portrait(self.task, original["revision_id"], mode="fit_pad")
        return original, fitted

    def batch_folder(self, record):
        return self.folder / "edits" / record["revision_id"] / "exports" / "batch" / record["export_id"]

    def test_whole_response_route_preserves_operator_and_snapshot_in_adoption_audit(self):
        preview = self.preview()
        selected = [candidate["id"] for candidate in reversed(preview["candidates"]["candidates"])]
        payload = routes.CreateResponseEdlRequest(proposal=self.proposal, preview_sha256=preview["preview_sha256"],
                                                   selected_ids=selected, actor="  集成验收  ")
        with patch.object(editing, "build_media_manifest", return_value=self.manifest):
            response = routes.create_response_edl(UUID(self.task), payload)
        self.assertEqual(response.status_code, 201)
        revision = json.loads(response.body)
        self.assertEqual(revision["actor"], "集成验收")
        saved = editing.load_task_edl(self.task, revision["revision_id"])
        event = reviewing.task_audit(self.task)["events"][0]
        self.assertEqual(event["actor"], "集成验收")
        self.assertEqual(event["kind"], "edl_adoption")
        self.assertEqual(event["candidate_snapshot_sha256"], revision["files"]["candidates.json"])
        self.assertEqual([clip["candidate_id"] for clip in event["after"]["clips"]], selected)
        self.assertEqual([clip["provenance"]["candidate_id"] for clip in saved["edl"]["clips"]], selected)
        self.assertEqual(saved["edl"]["duration_frames"], 1776)

    def test_reviewing_accepts_valid_whole_response_task_snapshot_without_old_candidate_fallback(self):
        preview = self.preview()
        self.write(self.folder / "candidates.json", preview["candidates"])
        state = reviewing.review_state(self.task)
        candidate_id = preview["candidates"]["candidates"][0]["id"]
        event = reviewing.create_review(self.task, candidate_id, "accepted", "集成验收", request_id=str(uuid4()),
                                        expected_snapshot_sha256=state["candidate_snapshot_sha256"])
        self.assertEqual(event["candidate_snapshot_sha256"], editing._digest(self.folder / "candidates.json"))
        self.write(self.folder / "candidates.json", self.old)
        self.assertTrue(all(item["decision"] == "undecided" for item in reviewing.review_state(self.task)["items"]))
        self.assertEqual(reviewing.review_request(self.task, event["request_id"]), event)
        self.assertEqual(reviewing.task_audit(self.task)["events"], [event])
        with self.assertRaises(editing.ProjectError) as caught:
            reviewing.create_review(self.task, candidate_id, "accepted", "集成验收", request_id=str(uuid4()))
        self.assertEqual(caught.exception.status_code, 422)

    def test_fitted_whole_response_manual_edit_and_batch_keep_schema_framing_snapshot_and_audit(self):
        original, fitted = self.fitted()
        fit = editing.load_task_edl(self.task, fitted["revision_id"])
        requests = [{"candidate_id": clip["provenance"]["candidate_id"],
                     "source_in_frame": clip["source_in_frame"], "source_out_frame": clip["source_out_frame"]}
                    for clip in reversed(fit["edl"]["clips"])]
        requests[0]["source_in_frame"] += 2
        edited = editing_changes.create_task_edit(self.task, fitted["revision_id"], requests, actor="复核教师")
        # Saved response snapshots remain authoritative when task rankings change.
        (self.folder / "candidates.json").write_text("changed current ranking", encoding="utf-8")
        saved = editing.load_task_edl(self.task, edited["revision_id"])
        self.assertEqual(saved["edl"]["schema_version"], "0.3.0")
        self.assertEqual(saved["edl"]["framing"], fit["edl"]["framing"])
        self.assertEqual(saved["edit_record"]["actor"], "复核教师")
        self.assertEqual(saved["revision"]["files"]["candidates.json"], original["files"]["candidates.json"])
        with patch("clipforge.editing.render.render_edl", side_effect=self.render_stub):
            exported = batch_export.create_task_batch_export(self.task, edited["revision_id"], str(uuid4()))
        loaded = batch_export.load_task_batch_export(self.task, edited["revision_id"], exported["export_id"])
        self.assertEqual(loaded["export"], exported)
        with ZipFile(self.batch_folder(exported) / "clips.zip") as archive:
            self.assertEqual(json.loads(archive.read("source.edl.json")), saved["edl"])
        for clip, item in zip(saved["edl"]["clips"], loaded["manifest"]["clips"], strict=True):
            self.assertEqual((item["candidate_id"], item["source_in_frame"], item["source_out_frame"]),
                             (clip["provenance"]["candidate_id"], clip["source_in_frame"], clip["source_out_frame"]))
            self.assertEqual(item["verification"]["framing"], fit["edl"]["framing"])
        audit = reviewing.task_audit(self.task)
        self.assertEqual([event["kind"] for event in audit["events"]], ["edl_adoption", "manual_edit"])
        self.assertEqual(audit["events"][-1]["actor"], "复核教师")
        self.assertEqual({item["kind"] for item in audit["events"][-1]["operations"]}, {"boundary_changed", "reordered"})

    def test_fitted_batch_rejects_missing_or_changed_framing_before_publication(self):
        _, fitted = self.fitted()
        for kind in ("missing", "different"):
            def invalid_framing(*args, **kwargs):
                report = self.render_stub(*args, **kwargs)
                if kind == "missing":
                    report.pop("framing")
                else:
                    report["framing"]["background"] = "white"
                return report
            request_id = str(uuid4())
            with self.subTest(kind=kind), patch("clipforge.editing.render.render_edl", side_effect=invalid_framing):
                with self.assertRaises(editing.ProjectError) as caught:
                    batch_export.create_task_batch_export(self.task, fitted["revision_id"], request_id)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertFalse(self.batch_folder({"revision_id": fitted["revision_id"], "export_id": request_id}).exists())

    def test_fitted_batch_rejects_resigned_manifest_framing_tampering(self):
        _, fitted = self.fitted()
        with patch("clipforge.editing.render.render_edl", side_effect=self.render_stub):
            record = batch_export.create_task_batch_export(self.task, fitted["revision_id"], str(uuid4()))
        folder = self.batch_folder(record)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["clips"][0]["verification"]["framing"]["sources"][0]["y"] += 2
        changed = json.dumps(manifest).encode("utf-8")
        (folder / "manifest.json").write_bytes(changed)
        with ZipFile(folder / "clips.zip") as archive:
            entries = [(item.filename, archive.read(item)) for item in archive.infolist()]
        with ZipFile(folder / "clips.zip", "w", compression=ZIP_STORED) as archive:
            for name, content in entries:
                archive.writestr(name, changed if name == "manifest.json" else content)
        record["files"] = {name: editing._digest(folder / name) for name in batch_export.FILES}
        record["output_bytes"] = (folder / "clips.zip").stat().st_size
        self.write(folder / "batch-export.json", record)
        with self.assertRaises(editing.ProjectError) as caught:
            batch_export.load_task_batch_export(self.task, fitted["revision_id"], record["export_id"])
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
