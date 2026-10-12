"""Saved MLT publication, binding and download integrity without media renders."""

from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from backend.tests import test_editing_service as fixtures
from clipforge.editing.mlt import export_mlt
from clipforge.services import editing, mlt_exporting, subtitling
from clipforge.storage import jobs


class MltExportingServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.revision = fixtures.EditingServiceTests.create(self)
        self.revision_id = self.revision["revision_id"]
        self.revision_folder = self.folder / "edits" / self.revision_id
        # XML geometry/text and real MLT are covered independently by core tests.
        # This deterministic stub isolates publication and semantic binding.
        def fake_export(project, source_root, **options):
            self.assertEqual(source_root, self.folder.resolve())
            return "<mlt>" + json.dumps({"edl": project, "options": options}, sort_keys=True,
                                       ensure_ascii=False) + "</mlt>\n"
        mocked = patch.object(mlt_exporting, "export_mlt", side_effect=fake_export)
        self.exporter = mocked.start()
        self.addCleanup(mocked.stop)

    def create(self, subtitle_id=None, revision_id=None):
        return mlt_exporting.create_task_mlt_export(
            self.task_id, revision_id or self.revision_id, subtitle_id=subtitle_id)

    def target(self, record):
        return self.folder / "edits" / record["revision_id"] / "exports" / "mlt" / record["export_id"]

    def legacy_subtitle_export(self):
        """Build a historical format 1 fixture containing genuine original-policy XML."""
        subtitle = subtitling.create_task_subtitles(self.task_id, self.revision_id, font_name="Microsoft YaHei")
        def legacy_export(project, source_root, **options):
            options['text_style_version'] = 1
            return export_mlt(project, source_root, **options)
        with patch.object(mlt_exporting, 'export_mlt', side_effect=legacy_export):
            record = self.create(subtitle['subtitle_id'])
        record['format_version'] = 1
        record.pop('text_style_version')
        (self.target(record) / 'mlt-export.json').write_text(json.dumps(record), encoding='utf-8')
        return record

    def test_plain_export_preserves_saved_bytes_source_and_legacy_task(self):
        before = jobs.get_job(self.task_id)
        original = {p.name: p.read_bytes() for p in self.revision_folder.iterdir() if p.is_file()}
        (self.folder / "candidates.json").write_text("current ranking unavailable", encoding="utf-8")
        record = self.create()
        self.assertEqual(mlt_exporting.load_task_mlt_export(
            self.task_id, self.revision_id, record["export_id"]), {"export": record})
        self.assertEqual(set(record["files"]), mlt_exporting.MLT_FILES)
        self.assertEqual((record['format_version'], record['text_style_version']), (2, 2))
        self.assertEqual((self.target(record) / "source.edl.json").read_bytes(), original["project.edl.json"])
        self.assertEqual(record["edl_file_sha256"], self.revision["files"]["project.edl.json"])
        self.assertTrue(all((self.revision_folder / n).read_bytes() == value for n, value in original.items()))
        self.assertEqual(jobs.get_job(self.task_id), before)
        self.assertEqual((self.folder / "normalized.mp4").read_bytes(), self.media)
        self.assertEqual((self.folder / "result.zip").read_bytes(), b"old ZIP unchanged")

    def test_subtitle_export_uses_saved_plan_font_and_exact_exports(self):
        subtitle = subtitling.create_task_subtitles(self.task_id, self.revision_id, font_name="Microsoft YaHei")
        source = self.revision_folder / "subtitles" / subtitle["subtitle_id"]
        original = {p.name: p.read_bytes() for p in source.iterdir()}
        with patch.dict(os.environ, {"SUBTITLE_FONT_NAME": "Different current env"}):
            record = self.create(subtitle["subtitle_id"])
            saved = mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record["export_id"])
        self.assertEqual(saved["export"], record)
        self.assertEqual(set(record["files"]), mlt_exporting.SUBTITLED_MLT_FILES)
        self.assertEqual(record["subtitle_font_name"], "Microsoft YaHei")
        for name in mlt_exporting.SUBTITLE_EXPORT_FILES:
            self.assertEqual((self.target(record) / name).read_bytes(), original[name])
            self.assertEqual(record["files"][name], subtitle["files"][name])
        self.assertTrue(all((source / n).read_bytes() == value for n, value in original.items()))
        options = self.exporter.call_args.kwargs
        self.assertEqual(options["font_name"], "Microsoft YaHei")
        self.assertEqual(options["subtitle_plan"]["edl_sha256"], record["edl_content_sha256"])
        self.assertEqual(set(options["analyses"]), {self.source["id"]})
        self.assertEqual(options['text_style_version'], 2)

    def test_genuine_legacy_export_and_new_style_download_with_their_saved_policies(self):
        legacy = self.legacy_subtitle_export()
        original = {p.name: p.read_bytes() for p in self.target(legacy).iterdir()}
        with patch.object(mlt_exporting, 'export_mlt', side_effect=export_mlt):
            current = self.create(legacy['subtitle_id'])
            self.assertEqual((current['format_version'], current['text_style_version']), (2, 2))
            for record, style in ((legacy, 'richText'), (current, 'dynamicText')):
                saved = mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record['export_id'])
                self.assertEqual(saved['export'], record)
                with mlt_exporting.open_mlt_export_file(
                        self.task_id, self.revision_id, record['export_id'], 'project.mlt') as stream:
                    content = stream.read()
                self.assertEqual(content, (self.target(record) / 'project.mlt').read_bytes())
                self.assertEqual(ET.fromstring(content).find("tractor/filter/property[@name='shotcut:filter']").text,
                                 style)
        self.assertEqual({p.name: p.read_bytes() for p in self.target(legacy).iterdir()}, original)

    def test_legacy_rehashed_xml_tamper_and_style_version_mismatch_are_rejected(self):
        legacy = self.legacy_subtitle_export()
        folder = self.target(legacy)
        xml = folder / 'project.mlt'
        original_xml = xml.read_bytes()
        root = ET.fromstring(original_xml)
        root.find("tractor/filter/property[@name='argument']").text = 'tampered words'
        xml.write_bytes(ET.tostring(root, encoding='utf-8'))
        metadata = deepcopy(legacy)
        metadata['files']['project.mlt'] = mlt_exporting._digest(xml)
        metadata_path = folder / 'mlt-export.json'
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        with patch.object(mlt_exporting, 'export_mlt', side_effect=export_mlt):
            with self.assertRaises(editing.ProjectError) as caught:
                mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, legacy['export_id'])
            self.assertEqual(caught.exception.status_code, 409)
            # Even a structurally valid format 2 header cannot relabel genuine v1 XML.
            xml.write_bytes(original_xml)
            metadata = {**legacy, 'format_version': 2, 'text_style_version': 2}
            metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
            with self.assertRaises(editing.ProjectError):
                mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, legacy['export_id'])

    def test_unknown_ambiguous_and_boolean_export_versions_are_rejected(self):
        record = self.create()
        path = self.target(record) / 'mlt-export.json'
        changes = [{'format_version': value} for value in (0, 3, True, 2.0, '2')]
        changes += [{'text_style_version': value} for value in (None, 1, 3, True, 2.0, '2')]
        changes += [{'format_version': 1, 'text_style_version': value} for value in (1, 2, None)]
        for change in changes:
            path.write_text(json.dumps({**record, **change}), encoding='utf-8')
            with self.subTest(change=change), self.assertRaises(editing.ProjectError) as caught:
                mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record['export_id'])
            self.assertEqual(caught.exception.status_code, 409)
        missing = deepcopy(record)
        missing.pop('text_style_version')
        path.write_text(json.dumps(missing), encoding='utf-8')
        with self.assertRaises(editing.ProjectError):
            mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record['export_id'])

    def test_portrait_geometry_and_original_parent_stay_bound(self):
        portrait = editing.create_task_portrait(self.task_id, self.revision_id)
        record = self.create(revision_id=portrait["revision_id"])
        project = editing.load_task_edl(self.task_id, portrait["revision_id"])["edl"]
        self.assertEqual(record["framing"], project["framing"])
        self.assertEqual(record["profile"], project["profile"])
        metadata = deepcopy(record)
        metadata["framing"]["sources"][0]["x"] += 2
        (self.target(record) / "mlt-export.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(editing.ProjectError):
            mlt_exporting.load_task_mlt_export(self.task_id, portrait["revision_id"], record["export_id"])

    def test_real_exporter_connects_saved_portrait_and_canonical_subtitle_filters(self):
        portrait = editing.create_task_portrait(self.task_id, self.revision_id)
        revision_id = portrait["revision_id"]
        subtitle = subtitling.create_task_subtitles(self.task_id, revision_id, font_name="Microsoft YaHei")
        plan = subtitling.load_task_subtitles(self.task_id, revision_id, subtitle["subtitle_id"])["plan"]
        with patch.object(mlt_exporting, "export_mlt", side_effect=export_mlt):
            plain = self.create()
            mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, plain["export_id"])
            record = self.create(subtitle["subtitle_id"], revision_id)
            mlt_exporting.load_task_mlt_export(self.task_id, revision_id, record["export_id"])
        xml = ET.parse(self.target(record) / "project.mlt").getroot()
        self.assertEqual((xml.find("profile").get("width"), xml.find("profile").get("height")), ("1080", "1920"))
        self.assertIsNotNone(xml.find("chain/filter[@id='crop0']"))
        filters = xml.findall("tractor/filter")
        self.assertEqual([(int(effect.get("in")), int(effect.get("out"))) for effect in filters[:-1]],
                         [(cue["timeline_in_frame"], cue["timeline_out_frame"] - 1) for cue in plan["cues"]])
        self.assertEqual(filters[-1].get("out"), str(plan["duration_frames"] - 1))
        self.assertEqual([effect.find("property[@name='argument']").text for effect in filters[:-1]],
                         [cue["text"] for cue in plan["cues"]])

    def test_tampered_export_bytes_rejected_even_after_updated_file_hash(self):
        record = self.create()
        folder = self.target(record)
        (folder / "project.mlt").write_text("<mlt>wrong order</mlt>", encoding="utf-8")
        metadata = deepcopy(record)
        metadata["files"]["project.mlt"] = mlt_exporting._digest(folder / "project.mlt")
        (folder / "mlt-export.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(editing.ProjectError):
            mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record["export_id"])
        for name in mlt_exporting.MLT_FILES:
            another = self.create()
            (self.target(another) / name).write_bytes(b"tampered")
            with self.subTest(name=name), self.assertRaises(editing.ProjectError):
                mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, another["export_id"])

    def test_subtitle_source_snapshot_identity_and_rehashed_copy_rejected(self):
        subtitle = subtitling.create_task_subtitles(self.task_id, self.revision_id)
        record = self.create(subtitle["subtitle_id"])
        folder = self.target(record)
        (folder / "captions.srt").write_text("wrong caption", encoding="utf-8")
        metadata = deepcopy(record)
        metadata["files"]["captions.srt"] = mlt_exporting._digest(folder / "captions.srt")
        (folder / "mlt-export.json").write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(editing.ProjectError):
            mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record["export_id"])
        other_revision = fixtures.EditingServiceTests.create(self)
        with self.assertRaises(editing.ProjectError):
            self.create(subtitle["subtitle_id"], other_revision["revision_id"])

    def test_metadata_strict_identity_frames_and_file_inventory(self):
        record = self.create()
        path = self.target(record) / "mlt-export.json"
        for change in ({"export_id": "other"}, {"source_task_id": "other"}, {"total_frames": True},
                       {"clip_count": False}, {"edl_file_sha256": "0" * 64}, {"cue_count": 5},
                       {"files": {**record["files"], "../secret.json": "0" * 64}}):
            path.write_text(json.dumps({**record, **change}), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(editing.ProjectError):
                mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record["export_id"])

    def test_present_but_null_or_invalid_subtitle_id_cannot_bypass_saved_plan_binding(self):
        subtitle = subtitling.create_task_subtitles(self.task_id, self.revision_id)
        record = self.create(subtitle["subtitle_id"])
        folder = self.target(record)
        metadata = deepcopy(record)
        # Keep real, valid extra subtitle files and their recorded hashes while
        # removing the optional metadata that used to expose this bypass.
        for name in ("subtitle_plan_sha256", "subtitle_font_name", "cue_count"):
            metadata.pop(name)
        for invalid in (None, "", "not-a-uuid", 42, True):
            metadata["subtitle_id"] = invalid
            (folder / "mlt-export.json").write_text(json.dumps(metadata), encoding="utf-8")
            with self.subTest(invalid=invalid), self.assertRaises(editing.ProjectError) as caught:
                mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record["export_id"])
            self.assertEqual(caught.exception.status_code, 409)

    def test_audio_single_frame_fails_closed_with_safe_mp4_alternative_before_export(self):
        saved = editing.load_task_edl(self.task_id, self.revision_id)
        project = deepcopy(saved["edl"])
        project["clips"] = [project["clips"][0]]
        project["clips"][0].update(source_out_frame=project["clips"][0]["source_in_frame"] + 1,
                                   timeline_in_frame=0, timeline_out_frame=1)
        project["duration_frames"] = 1
        path = self.revision_folder / "project.edl.json"
        path.write_text(json.dumps(project), encoding="utf-8")
        record = deepcopy(self.revision)
        record.update(duration_frames=1, clip_count=1)
        record["files"]["project.edl.json"] = mlt_exporting._digest(path)
        (self.revision_folder / "revision.json").write_text(json.dumps(record), encoding="utf-8")
        editing.load_task_edl(self.task_id, self.revision_id)
        with self.assertRaisesRegex(ValueError, "FFmpeg"):
            export_mlt(project, self.folder, require_media=False)
        with self.assertRaises(editing.ProjectError) as caught:
            self.create()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, "MLT 暂不支持含音轨的单帧片段，请使用同一 EDL 导出 MP4。")
        self.exporter.assert_not_called()
        self.assertFalse((self.revision_folder / "exports").exists())

    def test_failed_exports_and_collision_preserve_existing_version(self):
        first = self.create()
        original = {p.name: p.read_bytes() for p in self.target(first).iterdir()}
        with patch.object(mlt_exporting, "uuid4", return_value=first["export_id"]):
            with self.assertRaises(editing.ProjectError):
                self.create()
        with patch.object(mlt_exporting, "_write_json", side_effect=OSError("private disk details")):
            with self.assertRaises(editing.ProjectError) as caught:
                self.create()
            self.assertNotIn("private", caught.exception.detail)
        self.assertEqual({p.name: p.read_bytes() for p in self.target(first).iterdir()}, original)
        self.assertEqual([p.name for p in self.target(first).parent.iterdir()], [first["export_id"]])
        with patch.object(mlt_exporting, "export_mlt", side_effect=RuntimeError("private tool details")):
            with self.assertRaises(editing.ProjectError):
                self.create()
        self.assertEqual([p.name for p in self.target(first).parent.iterdir()], [first["export_id"]])

    def test_completion_last_with_inputs_held_and_new_versions_independent(self):
        writer = mlt_exporting._write_json
        def check_write(path, value):
            self.assertEqual(path.name, "mlt-export.json")
            self.assertEqual({p.name for p in path.parent.iterdir()}, mlt_exporting.MLT_FILES)
            if os.name == "nt":
                for target in (self.folder / "normalized.mp4", self.revision_folder / "project.edl.json"):
                    with self.assertRaises(PermissionError):
                        target.write_bytes(b"must remain locked")
            writer(path, value)
        with patch.object(mlt_exporting, "_write_json", side_effect=check_write):
            first = self.create()
        second = self.create()
        self.assertNotEqual(first["export_id"], second["export_id"])
        (self.target(first) / "mlt-export.json").unlink()
        with self.assertRaises(editing.ProjectError) as caught:
            mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, first["export_id"])
        self.assertEqual(caught.exception.status_code, 404)
        mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, second["export_id"])

    def test_download_allowlist_holds_verified_file_and_changed_media_rejected(self):
        record = self.create()
        for name in ("../project.mlt", "mlt-export.json", "normalized.mp4", "captions.srt"):
            with self.subTest(name=name), self.assertRaises(editing.ProjectError) as caught:
                with mlt_exporting.open_mlt_export_file(self.task_id, self.revision_id, record["export_id"], name):
                    self.fail("must reject path or absent subtitle")
            self.assertEqual(caught.exception.status_code, 404)
        with mlt_exporting.open_mlt_export_file(self.task_id, self.revision_id, record["export_id"], "project.mlt") as stream:
            if os.name == "nt":
                with self.assertRaises(PermissionError):
                    (self.target(record) / "project.mlt").write_bytes(b"must stay locked")
            self.assertTrue(stream.read().startswith(b"<mlt>"))
        self.assertTrue(stream.closed)
        (self.folder / "normalized.mp4").write_bytes(b"changed source")
        with self.assertRaises(editing.ProjectError):
            mlt_exporting.load_task_mlt_export(self.task_id, self.revision_id, record["export_id"])
        with self.assertRaises(editing.ProjectError):
            self.create()


if __name__ == "__main__":
    unittest.main()
