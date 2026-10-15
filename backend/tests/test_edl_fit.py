"""Full-frame portrait geometry and immutable legacy EDL contracts."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import unittest

from jsonschema import Draft202012Validator

from backend.tests.test_subtitles import edl_fixture, source_fixture
from clipforge.editing import edl


def fixture(width=1920, height=1080):
    source = source_fixture(fps={"num": 30000, "den": 1001})
    source.update(width=width, height=height)
    return edl_fixture([source], [("source", 90, 120), ("source", 0, 30)])


class PortraitFitEDLTests(unittest.TestCase):
    def test_complete_landscape_fits_without_changing_source_or_edit_decisions(self):
        original = fixture()
        before = deepcopy(original)
        fitted = edl.create_portrait_edl(original, mode="fit_pad")
        self.assertEqual(fitted["schema_version"], edl.PORTRAIT_FIT_VERSION)
        self.assertEqual(fitted["framing"], {"mode": "fit_pad", "background": "black", "sources": [
            {"source_id": "source", "x": 0, "y": 656, "width": 1080, "height": 608}]})
        self.assertEqual(fitted["profile"], {"width": 1080, "height": 1920,
                                             "fps": original["profile"]["fps"]})
        for key in ("sources", "clips", "duration_frames", "artifact_kind"):
            self.assertEqual(fitted[key], before[key])
        fitted["clips"][0]["provenance"]["source_sentences"].append("independent")
        self.assertEqual(original, before)

    def test_rounding_is_even_half_up_bounded_and_centered_for_varied_aspect_ratios(self):
        # Includes native portrait, tall, square, exact half-even ties, extreme
        # aspect ratios and one-pixel centering error from even origins.
        cases = ((1080, 1920, 1080, 1920), (2, 16384, 1080, 1920),
                 (16384, 2, 1080, 1920), (1080, 1080, 1080, 1920),
                 (1920, 1080, 90, 160), (4, 8, 18, 32),
                 (202, 350, 90, 160), (90, 162, 1080, 1920))
        for source_w, source_h, canvas_w, canvas_h in cases:
            with self.subTest(source=(source_w, source_h), canvas=(canvas_w, canvas_h)):
                project = edl.create_portrait_edl(fixture(source_w, source_h), canvas_w, canvas_h,
                                                  mode="fit_pad")
                rect = project["framing"]["sources"][0]
                scale = min(Fraction(canvas_w, source_w), Fraction(canvas_h, source_h))
                for axis, size, native, limit in (("x", "width", source_w, canvas_w),
                                                  ("y", "height", source_h, canvas_h)):
                    target = native * scale
                    # Independent search: nearest valid even size, ties upward.
                    oracle = min(range(2, limit + 1, 2), key=lambda candidate: (abs(candidate - target), -candidate))
                    self.assertEqual(rect[size], oracle)
                    self.assertEqual(rect[axis] % 2, 0)
                    self.assertLessEqual(abs(Fraction(limit - rect[size], 2) - rect[axis]), 1)
                    self.assertLessEqual(rect[axis] + rect[size], limit)
                edl.validate_edl(project)

    def test_each_native_source_has_its_own_canonical_foreground_rectangle(self):
        project = edl.create_portrait_edl(fixture(), mode="fit_pad")
        second = deepcopy(project["sources"][0])
        second.update(id="tall", width=1080, height=2160, sha256="b" * 64)
        project["sources"].append(second)
        project["framing"]["sources"].append(
            {"source_id": "tall", "x": 60, "y": 0, "width": 960, "height": 1920})
        edl.validate_edl(project)
        project["framing"]["sources"].reverse()
        with self.assertRaises(ValueError):
            edl.validate_edl(project)

    def test_background_geometry_version_and_source_mutations_fail_closed(self):
        fitted = edl.create_portrait_edl(fixture(), mode="fit_pad")
        mutations = (
            lambda value: value["framing"].pop("background"),
            lambda value: value["framing"].update(background="transparent"),
            lambda value: value["framing"].update(mode="center_crop"),
            lambda value: value["framing"]["sources"][0].update(y=654),
            lambda value: value["framing"]["sources"][0].update(height=606),
            lambda value: value["framing"]["sources"][0].update(x=False),
            lambda value: value["framing"]["sources"][0].update(width=1080.0),
            lambda value: value["framing"]["sources"][0].update(source_id="unknown"),
            lambda value: value["framing"]["sources"].clear(),
            lambda value: value.update(schema_version="0.2.0"),
            lambda value: value["sources"][0].update(width=1919),
            lambda value: value["profile"].update(width=1082),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                changed = deepcopy(fitted)
                mutation(changed)
                edl.validate_edl(changed)

    def test_only_explicit_supported_modes_and_original_even_inputs_are_accepted(self):
        for mode in (None, "blur", "", [], {}, True):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                edl.create_portrait_edl(fixture(), mode=mode)
        for width, height in ((1919, 1080), (1920, 1079)):
            with self.subTest(source=(width, height)), self.assertRaises(ValueError):
                edl.create_portrait_edl(fixture(width, height), mode="fit_pad")
        for dimensions in ((True, 1920), (1080.0, 1920), (1080, 1921), (1920, 1080)):
            with self.subTest(output=dimensions), self.assertRaises(ValueError):
                edl.create_portrait_edl(fixture(), *dimensions, mode="fit_pad")
        fitted = edl.create_portrait_edl(fixture(), mode="fit_pad")
        with self.assertRaisesRegex(ValueError, "0.1.0"):
            edl.create_portrait_edl(fitted, mode="fit_pad")

    def test_legacy_schemas_and_default_center_crop_contract_remain_unchanged(self):
        digests = {"edl.schema.json": "44e6c89aceeb095765c768ea59069df82437be8fee0a8c4c16e5df36ad9c26e8",
                   "edl-v0.2.schema.json": "fe5b54e1ac01fa10664d36b9ce505509db43a43ece4607395b54c73af8d748fe"}
        for filename, digest in digests.items():
            # Compare canonical repository bytes across LF/CRLF checkouts.
            data = (edl.ROOT / "schemas" / filename).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertEqual(edl.create_portrait_edl(fixture()),
                         edl.create_portrait_edl(fixture(), mode="center_crop"))
        self.assertEqual(edl.create_portrait_edl(fixture())["schema_version"], "0.2.0")
        schema = edl._load_schema(edl.PORTRAIT_FIT_VERSION)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(edl.create_portrait_edl(fixture(), mode="fit_pad"))
        for name in ("source", "clip", "profile"):
            self.assertEqual(schema["$defs"][name], edl._load_schema()["$defs"][name])


if __name__ == "__main__":
    unittest.main()
