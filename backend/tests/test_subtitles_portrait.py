"""Portrait grouping/font sizing changes layout without changing word timing."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import re
import unittest

from clipforge.editing.edl import create_portrait_edl
from clipforge.editing import subtitles
from backend.tests.test_subtitles import analysis_fixture, edl_fixture, source_fixture


def styles(ass):
    return {line.split(",", 1)[0][7:]: line.split(",") for line in ass.splitlines() if line.startswith("Style: ")}


class PortraitSubtitleTests(unittest.TestCase):
    def test_existing_horizontal_plan_and_serializations_remain_byte_compatible(self):
        plan = subtitles.build_subtitles(edl_fixture(), {"source": analysis_fixture()})
        expected = ("1521f95174cecf1641c340ef00b63a3f0efb9e7668281b2d0f9856d24c4fef3d",
                    "86e38ec36107de2303c615be81eafc4f059e4d386f31bce154e2807de63ed6a2",
                    "0ba4fefce703b9c12cefb1dfbfd9d597e4e5b3723d97c4e5b18f68811627fe08")
        actual = [hashlib.sha256(value.encode("utf-8")).hexdigest()
                  for value in (subtitles._canonical(plan), subtitles.to_srt(plan), subtitles.to_ass(plan))]
        self.assertEqual(tuple(actual), expected)
        self.assertEqual(plan["policy"], subtitles.POLICY)

    def test_portrait_regroups_to_twelve_cjk_columns_without_changing_any_word_mapping(self):
        source = source_fixture()
        source.update(width=1920, height=1080)
        edl = edl_fixture([source], [("source", 0, 150)])
        words = [(index / 10, (index + 1) / 10, "字", 0) for index in range(35)]
        analyses = {"source": analysis_fixture(source, words)}
        before = deepcopy((edl, analyses, subtitles.POLICY))
        horizontal = subtitles.build_subtitles(edl, analyses)
        portrait_edl = create_portrait_edl(edl)
        portrait = subtitles.build_subtitles(portrait_edl, analyses)
        self.assertEqual((edl, analyses, subtitles.POLICY), before)
        self.assertEqual(portrait["policy"], subtitles.PORTRAIT_POLICY)
        self.assertEqual(portrait["profile"], portrait_edl["profile"])
        self.assertEqual([len(cue["word_ids"]) for cue in portrait["cues"]], [24, 11])
        self.assertEqual([word for cue in portrait["cues"] for word in cue["word_mappings"]],
                         [word for cue in horizontal["cues"] for word in cue["word_mappings"]])
        for cue in portrait["cues"]:
            self.assertLessEqual(len(cue["text"].splitlines()), 2)
            self.assertTrue(all(subtitles._columns(line) <= 24 for line in cue["text"].splitlines()))
        subtitles.validate_subtitles(portrait, portrait_edl, analyses)

    def test_tiny_portrait_fonts_and_brand_fit_inside_output_margins(self):
        edl = edl_fixture(ranges=[("source", 0, 60)])
        plan = subtitles.build_subtitles(create_portrait_edl(edl, 90, 160),
                                         {"source": analysis_fixture(words=[(0, 1.5, "字" * 18, 0)])})
        ass, width = subtitles.to_ass(plan), 90
        parsed = styles(ass)
        self.assertIn("PlayResX: 90", ass)
        self.assertIn("PlayResY: 160", ass)
        self.assertLess(int(parsed["Subtitle"][2]), 8)
        for name, text in (("Subtitle", plan["cues"][0]["text"]), ("Brand", plan["brand"]["text"])):
            style = parsed[name]
            size, margin = int(style[2]), int(style[19])
            self.assertGreater(size, 0)
            for line in text.splitlines():
                self.assertLessEqual(Fraction(subtitles._columns(line) * size * 23, 40) + 4,
                                     width - 2 * margin)
        self.assertEqual(plan["cues"][0]["word_mappings"][0]["text"], "字" * 18)
        self.assertIn("AI辅助剪辑", ass)

    def test_long_word_overlap_shrinks_with_trusted_numeric_override_and_keeps_literal_controls(self):
        edl = edl_fixture(ranges=[("source", 0, 150)])
        raw = "字" * 50 + r"{\fs999\pos(1,1)}"
        plan = subtitles.build_subtitles(create_portrait_edl(edl), {"source": analysis_fixture(
            words=[(0, 3, raw, 0), (1, 3.5, "重叠" * 5, 0)])})
        ass = subtitles.to_ass(plan)
        self.assertEqual(len(plan["cues"]), 1)
        self.assertIn("line_width_exception", plan["cues"][0]["flags"])
        event = next(line for line in ass.splitlines() if line.startswith("Dialogue: 0,"))
        display = event.split(",", 9)[9]
        override = re.match(r"\{\\fs([0-9]+)\}", display)
        self.assertIsNotNone(override)
        self.assertNotIn(r"\fs999", display)
        self.assertNotIn(r"\pos", display)
        self.assertIn("｛＼fs999＼pos(1,1)｝", display.replace(r"\N", ""))
        size = int(override.group(1))
        self.assertLess(size, int(styles(ass)["Subtitle"][2]))
        for line in display[override.end():].split(r"\N"):
            self.assertLessEqual(Fraction(subtitles._columns(line) * size * 23, 40) + 4, 1080 - 108)
        self.assertEqual(plan["cues"][0]["word_mappings"][0]["text"], raw)

    def test_portrait_policy_is_fixed_and_requires_matching_even_output_profile(self):
        edl = create_portrait_edl(edl_fixture())
        plan = subtitles.build_subtitles(edl, {"source": analysis_fixture()})
        for mutation in (lambda value: value["profile"].update(width=160, height=90),
                         lambda value: value["profile"].update(width=1081),
                         lambda value: value["policy"].update(target_cjk_characters_per_line=18),
                         lambda value: value["policy"].update(font_sizing="arbitrary")):
            with self.subTest(mutation=mutation):
                changed = deepcopy(plan)
                mutation(changed)
                for serializer in (subtitles.to_srt, subtitles.to_ass):
                    with self.assertRaises(ValueError):
                        serializer(changed)

    def test_wide_latin_glyphs_use_width_bound_instead_of_terminal_column_estimate(self):
        raw = "W" * 24
        plan = subtitles.build_subtitles(create_portrait_edl(edl_fixture()),
                                         {"source": analysis_fixture(words=[(1, 1.5, raw, 0)])})
        ass = subtitles.to_ass(plan)
        event = next(line for line in ass.splitlines() if line.startswith("Dialogue: 0,"))
        display = event.split(",", 9)[9]
        override = re.match(r"\{\\fs([0-9]+)\}", display)
        self.assertIsNotNone(override)
        size = int(override.group(1))
        self.assertLess(size, int(styles(ass)["Subtitle"][2]))
        self.assertLessEqual(Fraction(len(raw) * size * 23, 20) + 4, 1080 - 108)
        self.assertEqual(display[override.end():], raw)

    def test_rational_repeat_reorder_and_clipped_word_timing_remain_inherited(self):
        source = source_fixture(fps={"num": 30000, "den": 1001})
        edl = edl_fixture([source], [("source", 60, 90), ("source", 30, 60), ("source", 30, 60)])
        analyses = {"source": analysis_fixture(source, [(.9, 1.3, "裁头", 0), (1.3, 1.8, "原文", 0),
                                                        (1.8, 2.2, "裁尾", 1), (2.2, 2.8, "后段", 1)])}
        original = subtitles.build_subtitles(edl, analyses)
        portrait_edl = create_portrait_edl(edl, 180, 320)
        portrait = subtitles.build_subtitles(portrait_edl, analyses)
        self.assertEqual(portrait["sources"], original["sources"])
        self.assertEqual(portrait["clip_reports"], original["clip_reports"])
        self.assertEqual([word for cue in portrait["cues"] for word in cue["word_mappings"]],
                         [word for cue in original["cues"] for word in cue["word_mappings"]])
        self.assertEqual(subtitles.to_srt(portrait), subtitles.to_srt(original))
        subtitles.validate_subtitles(portrait, portrait_edl, analyses)


if __name__ == "__main__":
    unittest.main()
