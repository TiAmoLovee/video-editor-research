"""Versioned portrait framing retains native facts and exact edit decisions."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from clipforge.editing import edl
from backend.tests.test_subtitles import edl_fixture, source_fixture


def landscape_fixture(*, fps=None):
    source = source_fixture(fps=fps)
    source.update(width=1920, height=1080)
    return edl_fixture([source], [("source", 90, 120), ("source", 0, 30)])


class PortraitEDLTests(unittest.TestCase):
    def test_default_crop_and_output_preserve_original_edit_and_media_facts(self):
        original = landscape_fixture(fps={"num": 30000, "den": 1001})
        before = deepcopy(original)
        portrait = edl.create_portrait_edl(original)
        self.assertEqual(original, before)
        self.assertEqual(portrait["schema_version"], "0.2.0")
        self.assertEqual(portrait["profile"], {"width": 1080, "height": 1920, "fps": original["profile"]["fps"]})
        self.assertEqual(portrait["framing"], {"mode": "center_crop", "sources": [
            {"source_id": "source", "x": 662, "y": 12, "width": 594, "height": 1056}]})
        for key in ("sources", "clips", "duration_frames", "artifact_kind"):
            self.assertEqual(portrait[key], original[key])
        portrait["clips"][0]["provenance"]["source_sentences"].append("independent-copy")
        self.assertEqual(original, before)

    def test_small_and_native_portrait_crops_are_even_centered_largest_exact_ratio(self):
        for size, expected in (((160, 90), (62, 12, 36, 64)),
                               ((1080, 1920), (0, 0, 1080, 1920)),
                               ((202, 350), (10, 14, 180, 320))):
            with self.subTest(size=size):
                source = source_fixture()
                source.update(width=size[0], height=size[1])
                portrait = edl.create_portrait_edl(edl_fixture([source]), 90, 160)
                crop = portrait["framing"]["sources"][0]
                self.assertEqual(tuple(crop[key] for key in ("x", "y", "width", "height")), expected)
                for position, length, native in ((crop["x"], crop["width"], size[0]),
                                                 (crop["y"], crop["height"], size[1])):
                    self.assertEqual(position % 2, 0)
                    self.assertLessEqual(abs(position - (native - length) / 2), 1)

    def test_every_source_requires_canonical_crop_with_native_fps(self):
        first, second = source_fixture(), source_fixture("other")
        first.update(width=1920, height=1080)
        second.update(width=1920, height=1080)
        original = edl_fixture([first, second], [("other", 0, 30), ("source", 30, 60)])
        portrait = edl.create_portrait_edl(original)
        self.assertEqual([crop["source_id"] for crop in portrait["framing"]["sources"]], ["source", "other"])
        self.assertEqual([clip["source_id"] for clip in portrait["clips"]], ["other", "source"])
        # A v0.2 declaration supports separately measured native resolutions.
        portrait["sources"][1].update(width=160, height=90)
        portrait["framing"]["sources"][1] = {"source_id": "other", "x": 62, "y": 12, "width": 36, "height": 64}
        edl.validate_edl(portrait)
        portrait["sources"][1]["fps"] = {"num": 25, "den": 1}
        with self.assertRaisesRegex(ValueError, "fps"):
            edl.validate_edl(portrait)

    def test_geometry_and_source_contract_tampering_fails_closed(self):
        original = edl.create_portrait_edl(landscape_fixture())
        mutations = (lambda value: value.pop("framing"),
                     lambda value: value["framing"].update(mode="subject_tracking"),
                     lambda value: value["framing"]["sources"].clear(),
                     lambda value: value["framing"]["sources"].append(deepcopy(value["framing"]["sources"][0])),
                     lambda value: value["framing"]["sources"][0].update(source_id="unknown"),
                     lambda value: value["framing"]["sources"][0].update(x=664),
                     lambda value: value["framing"]["sources"][0].update(x=661),
                     lambda value: value["framing"]["sources"][0].update(x=2000),
                     lambda value: value["framing"]["sources"][0].update(width=592),
                     lambda value: value["framing"]["sources"][0].update(y=12.0),
                     lambda value: value["framing"]["sources"][0].update(x=False),
                     lambda value: value["sources"][0].update(width=1080, height=1920),
                     lambda value: value["sources"][0].update(colorspace=601),
                     lambda value: value["sources"][0].update(sha256="f" * 64),
                     lambda value: value["clips"][0].update(timeline_in_frame=1),
                     lambda value: value["profile"].update(width=1082))
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                changed = deepcopy(original)
                mutation(changed)
                edl.validate_edl(changed)

    def test_conversion_only_accepts_valid_v01_and_even_nine_by_sixteen_output(self):
        for dimensions in ((1080.0, 1920), (True, 1920), (1080, 1921), (1920, 1080),
                           (0, 1920), (18000, 32000)):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                edl.create_portrait_edl(landscape_fixture(), *dimensions)
        with self.assertRaisesRegex(ValueError, "0.1.0"):
            edl.create_portrait_edl(edl.create_portrait_edl(landscape_fixture()))
        for dimensions in ((1919, 1080), (16, 30)):
            source = source_fixture()
            source.update(width=dimensions[0], height=dimensions[1])
            with self.assertRaises(ValueError):
                edl.create_portrait_edl(edl_fixture([source]))
        for invalid in (None, [], {}, {"schema_version": "9.0.0"}):
            with self.assertRaises(ValueError):
                edl.validate_edl(invalid)

    def test_original_schema_bytes_and_v01_dimension_contract_are_unchanged(self):
        original = edl.ROOT / "schemas/edl.schema.json"
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),
                         "44e6c89aceeb095765c768ea59069df82437be8fee0a8c4c16e5df36ad9c26e8")
        self.assertEqual(edl._load_schema(), edl._load_schema("0.1.0"))
        old, new = edl._load_schema(), edl._load_schema("0.2.0")
        self.assertEqual(new["$defs"]["source"], old["$defs"]["source"])
        Draft202012Validator.check_schema(new)
        Draft202012Validator(new).validate(edl.create_portrait_edl(landscape_fixture()))
        changed = landscape_fixture()
        changed["profile"].update(width=1080, height=1920)
        with self.assertRaisesRegex(ValueError, "match"):
            edl.validate_edl(changed)

    def test_versioned_schema_uses_packaged_root_without_remote_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schemas").mkdir()
            for filename in ("edl.schema.json", "edl-v0.2.schema.json"):
                content = (edl.ROOT / "schemas" / filename).read_text(encoding="utf-8")
                (root / "schemas" / filename).write_text(content, encoding="utf-8")
            with patch.object(edl, "ROOT", root):
                portrait = edl.create_portrait_edl(landscape_fixture())
                edl.validate_edl(portrait)
                (root / "schemas/edl-v0.2.schema.json").unlink()
                edl.validate_edl(landscape_fixture())
                with self.assertRaises(FileNotFoundError):
                    edl.validate_edl(portrait)


if __name__ == "__main__":
    unittest.main()
