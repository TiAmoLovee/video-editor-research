"""Edited EDLs feed exact saved cuts/order to real MLT XML and the MP4 renderer boundary."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from backend.tests import test_editing_service as fixtures
from clipforge.services import editing, editing_changes, mlt_exporting, rendering


class EditedExportTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)

    def create(self, selected=None):
        return fixtures.EditingServiceTests.create(self, selected=selected)

    def edited(self, portrait):
        parent = self.create()
        if portrait:
            parent = editing.create_task_portrait(self.task_id, parent["revision_id"])
        saved = editing.load_task_edl(self.task_id, parent["revision_id"])
        requests = [{"candidate_id": clip["provenance"]["candidate_id"],
                     "source_in_frame": clip["source_in_frame"] + 3,
                     "source_out_frame": clip["source_out_frame"] - 2}
                    for clip in reversed(saved["edl"]["clips"])]
        record = editing_changes.create_task_edit(self.task_id, parent["revision_id"], requests)
        return record, editing.load_task_edl(self.task_id, record["revision_id"])["edl"]

    def test_real_mlt_export_uses_edited_landscape_and_portrait_cuts(self):
        for portrait in (False, True):
            with self.subTest(portrait=portrait):
                revision, project = self.edited(portrait)
                exported = mlt_exporting.create_task_mlt_export(self.task_id, revision["revision_id"])
                folder = self.folder / "edits" / revision["revision_id"] / "exports/mlt" / exported["export_id"]
                xml = ET.parse(folder / "project.mlt").getroot()
                entries = xml.findall("playlist[@id='playlist0']/entry")
                self.assertEqual([(int(entry.get("in")), int(entry.get("out"))) for entry in entries],
                                 [(clip["source_in_frame"], clip["source_out_frame"] - 1)
                                  for clip in project["clips"]])
                self.assertEqual(json.loads((folder / "source.edl.json").read_text(encoding="utf-8")), project)
                self.assertEqual(int(xml.find("profile").get("width")), project["profile"]["width"])
                self.assertEqual(exported["total_frames"], project["duration_frames"])
                self.assertEqual(mlt_exporting.load_task_mlt_export(
                    self.task_id, revision["revision_id"], exported["export_id"])["export"], exported)

    def test_mp4_service_passes_exact_edited_edl_without_inheriting_parent_artifacts(self):
        for portrait in (False, True):
            with self.subTest(portrait=portrait):
                revision, expected = self.edited(portrait)

                def render(project, source_root, output, **options):
                    self.assertEqual(project, expected)
                    self.assertEqual(source_root, self.folder.resolve())
                    Path(output).write_bytes(b"mock render; no real frame/media acceptance claim")
                    return {"artifact_kind": "edl_render_verification", "edl_sha256": rendering._content_hash(project),
                            "framing": project.get("framing"),
                            "output": {"path": str(output), "sha256": rendering._digest(output), "total_frames": project["duration_frames"],
                                       "profile": project["profile"]}}

                with patch("clipforge.editing.render.render_edl", side_effect=render) as renderer:
                    artifact = rendering.create_task_render(self.task_id, revision["revision_id"])
                renderer.assert_called_once()
                folder = self.folder / "edits" / revision["revision_id"] / "renders" / artifact["render_id"]
                self.assertEqual(json.loads((folder / "source.edl.json").read_text(encoding="utf-8")), expected)
                self.assertEqual(artifact["total_frames"], expected["duration_frames"])
                self.assertNotIn("subtitle_id", artifact)


if __name__ == "__main__":
    unittest.main()
