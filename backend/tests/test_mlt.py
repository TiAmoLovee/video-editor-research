"""XML range arithmetic, source reuse, escaped local references, and CLI checks."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from clipforge.editing.edl import candidates_to_edl, validate_edl
from clipforge.editing.mlt import export_mlt, resolve_source_ref
from backend.tests.test_edl import candidate_fixture, edl_fixture, source_fixture


class MLTTests(unittest.TestCase):
    def test_inclusive_points_repeated_source_order_and_xml_escaping(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = edl_fixture()
            media = folder / data["sources"][0]["ref"]
            media.touch()
            data["clips"].reverse()
            cursor = 0
            for clip in data["clips"]:
                clip["timeline_in_frame"] = cursor
                cursor += clip["source_out_frame"] - clip["source_in_frame"]
                clip["timeline_out_frame"] = cursor
            xml = export_mlt(data, folder)
            root = ET.fromstring(xml)
            entries = root.findall("playlist[@id='playlist0']/entry")
            self.assertEqual([(e.get("in"), e.get("out")) for e in entries], [("918", "1237"), ("0", "599")])
            self.assertEqual([e.get("producer") for e in entries], ["chain0", "chain0_clip1"])
            self.assertEqual(len(root.findall("chain")), 2)
            self.assertEqual([chain.find("property[@name='resource']").text for chain in root.findall('chain')],
                             [media.resolve().as_posix()] * 2)
            self.assertEqual(root.find("chain/property[@name='resource']").text, media.resolve().as_posix())
            self.assertIn("&amp;", xml)
            self.assertEqual(root.find("tractor").get("out"), "919")
            self.assertEqual(root.find("producer[@id='black']").get("out"), "919")
            self.assertEqual(root.get("producer"), "main_bin")
            self.assertEqual(root.find("tractor/property[@name='shotcut']").text, "1")
            data["sources"][0]["ref"] = "XML <source>.mp4"
            self.assertIn("&lt;source&gt;", export_mlt(data, folder, require_media=False))

    def test_changed_media_content_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = edl_fixture()
            media = folder / data["sources"][0]["ref"]
            media.write_bytes(b"different media content")
            with self.assertRaisesRegex(ValueError, "sha256"):
                export_mlt(data, folder)

    def test_known_shotcut_sample_range_lengths(self):
        source = source_fixture(total_frames=1238)
        candidates = candidate_fixture(((0, 20.033333333333335), (30.6, 41.266666666666666)), source)
        data = candidates_to_edl(candidates, ["c0", "c1"], source)
        self.assertEqual(data["duration_frames"], 921)
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_mlt(data, directory, require_media=False))
            entries = root.findall("playlist[@id='playlist0']/entry")
            self.assertEqual([(e.get("in"), e.get("out")) for e in entries], [("0", "600"), ("918", "1237")])
            self.assertEqual(root.find("tractor").get("out"), "920")

    def test_single_frame_final_frame_and_silent_source(self):
        source = source_fixture(total_frames=3, audio_stream_index=None)
        data = candidates_to_edl(candidate_fixture(((0.06666666666666667, 0.1),), source), ["c0"], source)
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_mlt(data, directory, require_media=False))
            entry = root.find("playlist[@id='playlist0']/entry")
            self.assertEqual((entry.get("in"), entry.get("out")), ("2", "2"))
            self.assertEqual(root.find("tractor").get("out"), "0")
            self.assertEqual(root.find("chain/property[@name='audio_index']").text, "-1")

    def test_rational_profile_and_multiple_sources(self):
        first = source_fixture(fps={"num": 30000, "den": 1001})
        data = candidates_to_edl(candidate_fixture(((1.001, 2.002), (3.003, 4.004)), first),
                                 ["c0", "c1"], first)
        second = deepcopy(first)
        second.update(id="normalized-2", ref="second.mp4", sha256="b" * 64)
        data["sources"].append(second)
        data["clips"][1]["source_id"] = second["id"]
        second_edl = candidates_to_edl(candidate_fixture(((1.001, 2.002), (3.003, 4.004)), second),
                                       ["c1"], second)
        data["clips"][1]["provenance"] = second_edl["clips"][0]["provenance"]
        validate_edl(data)
        with tempfile.TemporaryDirectory() as directory:
            root = ET.fromstring(export_mlt(data, directory, require_media=False))
            profile = root.find("profile")
            self.assertEqual((profile.get("frame_rate_num"), profile.get("frame_rate_den")), ("30000", "1001"))
            self.assertEqual((profile.get("display_aspect_num"), profile.get("display_aspect_den")), ("8", "5"))
            self.assertEqual([e.get("producer") for e in root.findall("playlist[@id='playlist0']/entry")],
                             ["chain0", "chain1"])
            self.assertEqual([(e.get("in"), e.get("out")) for e in root.findall("playlist[@id='playlist0']/entry")],
                             [("30", "59"), ("90", "119")])

    def test_relative_root_and_local_only_references(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            media = folder / "one.mp4"
            media.touch()
            self.assertEqual(resolve_source_ref("one.mp4", folder), media.resolve().as_posix())
            self.assertEqual(resolve_source_ref(str(media.resolve()), folder), media.resolve().as_posix())
            for ref in ("../escape.mp4", "sub/../../escape.mp4", "https://example.invalid/a.mp4",
                        "file:///a.mp4", "//server/share.mp4", "\\\\server\\share.mp4", "C:relative.mp4",
                        "one.mp4:stream", "bad\x00.mp4", " trailing.mp4", ""):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    resolve_source_ref(ref, folder, require_media=False)
            with self.assertRaisesRegex(ValueError, "absolute"):
                resolve_source_ref("one.mp4", ".")
            with self.assertRaisesRegex(ValueError, "directory"):
                resolve_source_ref("one.mp4", folder / "absent")
            with self.assertRaisesRegex(ValueError, "does not exist"):
                resolve_source_ref("absent.mp4", folder)

    def test_invalid_edl_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as directory:
            data = edl_fixture()
            data["clips"][0]["source_out_frame"] = 3001
            with self.assertRaises(ValueError):
                export_mlt(data, directory, require_media=False)

    def test_cli_local_export_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            data = edl_fixture()
            (folder / data["sources"][0]["ref"]).touch()
            source, output = folder / "edl.json", folder / "project.mlt"
            source.write_text(json.dumps(data), encoding="utf-8")
            command = [sys.executable, "-m", "clipforge.editing.mlt", str(source),
                       "--source-root", str(folder), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(ET.parse(output).getroot().findall("playlist[@id='playlist0']/entry")), 2)
            before = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
