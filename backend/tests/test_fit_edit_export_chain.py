"""A fitted manual edit stays bound through saved subtitles and both exporters.

MLT XML and subtitle plans are real; MP4 media execution alone is stubbed here.
Native renderer image/audio behavior is verified separately with real media.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from backend.tests import test_editing_service as fixtures
from clipforge.editing.subtitles import to_ass
from clipforge.services import editing, editing_changes, mlt_exporting, rendering, subtitling


class FitEditExportChainTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.original = fixtures.EditingServiceTests.create(self)
        fitted = editing.create_task_portrait(self.task_id, self.original["revision_id"], mode="fit_pad")
        self.fitted = editing.load_task_edl(self.task_id, fitted["revision_id"])
        requests = [{"candidate_id": clip["provenance"]["candidate_id"],
                     "source_in_frame": clip["source_in_frame"],
                     "source_out_frame": clip["source_out_frame"]}
                    for clip in reversed(self.fitted["edl"]["clips"])]
        requests[0]["source_in_frame"] += 1
        self.edited = editing_changes.create_task_edit(self.task_id, fitted["revision_id"], requests)
        self.revision_id = self.edited["revision_id"]
        self.saved = editing.load_task_edl(self.task_id, self.revision_id)
        self.subtitle = subtitling.create_task_subtitles(
            self.task_id, self.revision_id, font_name="Noto Sans CJK SC")
        self.subtitle_id = self.subtitle["subtitle_id"]
        self.plan = subtitling.load_task_subtitles(
            self.task_id, self.revision_id, self.subtitle_id)["plan"]

    def test_edited_fit_subtitles_and_saved_mlt_keep_exact_framing_timeline_and_baseline(self):
        project = self.saved["edl"]
        self.assertEqual(project["schema_version"], "0.3.0")
        self.assertEqual(project["framing"], self.fitted["edl"]["framing"])
        self.assertEqual(self.saved["edit_record"]["baseline_revision_id"],
                         self.fitted["revision"]["revision_id"])
        self.assertEqual(self.saved["edit_record"]["effort"]["adjusted_clip_count"], 1)
        self.assertEqual(self.plan["policy"]["layout"], "portrait_9_16")
        exported = mlt_exporting.create_task_mlt_export(
            self.task_id, self.revision_id, subtitle_id=self.subtitle_id)
        self.assertEqual(mlt_exporting.load_task_mlt_export(
            self.task_id, self.revision_id, exported["export_id"])["export"], exported)
        self.assertEqual(exported["framing"], project["framing"])
        self.assertEqual(exported["edl_content_sha256"], rendering._content_hash(project))
        with mlt_exporting.open_mlt_export_file(
                self.task_id, self.revision_id, exported["export_id"], "project.mlt") as incoming:
            root = ET.fromstring(incoming.read())
        entries = root.findall("playlist[@id='playlist0']/entry")
        self.assertEqual([(int(item.get("in")), int(item.get("out")) + 1) for item in entries],
                         [(clip["source_in_frame"], clip["source_out_frame"]) for clip in project["clips"]])
        fit = root.find("chain/filter/property[@name='clipforge:framing']")
        self.assertIsNotNone(fit)
        self.assertEqual(fit.text, "fit_pad")
        rectangle = project["framing"]["sources"][0]
        geometry = root.find("chain/filter/property[@name='transition.rect']")
        self.assertEqual(geometry.text,
                         f"{rectangle['x']} {rectangle['y']} {rectangle['width']} {rectangle['height']} 1")
        self.assertTrue(root.findall("tractor/filter/property[@name='argument']"))

    def test_edited_fit_render_requires_matching_framing_and_keeps_saved_subtitle_binding(self):
        project = self.saved["edl"]

        def render_stub(value, source_root, output, **options):
            self.assertEqual(value, project)
            self.assertEqual(source_root, self.folder.resolve())
            self.assertEqual(options["subtitle_plan"], self.plan)
            Path(output).write_bytes(b"stub MP4: service binding only")
            ass = to_ass(self.plan, font_name="Noto Sans CJK SC")
            return {"schema_version": "0.1.0", "artifact_kind": "edl_render_verification",
                    "edl_sha256": rendering._content_hash(value), "framing": deepcopy(value["framing"]),
                    "output": {"path": str(output), "sha256": rendering._digest(output),
                               "total_frames": value["duration_frames"], "profile": value["profile"]},
                    "subtitles": {"plan_sha256": rendering._content_hash(self.plan),
                                  "font_name": "Noto Sans CJK SC",
                                  "ass_sha256": hashlib.sha256(ass.encode("utf-8")).hexdigest()}}

        def wrong_framing(*args, **kwargs):
            report = render_stub(*args, **kwargs)
            report["framing"]["sources"][0]["y"] += 2
            return report

        with patch("clipforge.editing.render.render_edl", side_effect=wrong_framing):
            with self.assertRaises(editing.ProjectError) as caught:
                rendering.create_task_render(self.task_id, self.revision_id, subtitle_id=self.subtitle_id)
        self.assertEqual(caught.exception.status_code, 409)
        with patch("clipforge.editing.render.render_edl", side_effect=render_stub):
            record = rendering.create_task_render(self.task_id, self.revision_id, subtitle_id=self.subtitle_id)
        loaded = rendering.load_task_render(self.task_id, self.revision_id, record["render_id"])
        self.assertEqual(loaded["render"]["subtitle_id"], self.subtitle_id)
        self.assertEqual(loaded["verification"]["framing"], project["framing"])
        folder = self.folder / "edits" / self.revision_id / "renders" / record["render_id"]
        report = loaded["verification"]
        report["framing"]["background"] = "white"
        (folder / "verification.json").write_text(json.dumps(report), encoding="utf-8")
        metadata = deepcopy(record)
        metadata["files"]["verification.json"] = editing._digest(folder / "verification.json")
        (folder / "render.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(editing.ProjectError) as caught:
            rendering.load_task_render(self.task_id, self.revision_id, record["render_id"])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(json.loads((folder / "source.edl.json").read_text(encoding="utf-8")), project)


if __name__ == "__main__":
    unittest.main()
