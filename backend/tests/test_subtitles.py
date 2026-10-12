"""Saved-word projection, clipping evidence, immutable bindings and safe serialization."""

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from clipforge.editing import subtitles


def source_fixture(identifier="source", *, audio=True, fps=None):
    return {"id": identifier, "ref": identifier + ".mp4", "sha256": ("a" if identifier == "source" else "b") * 64,
            "total_frames": 300, "width": 160, "height": 90, "fps": fps or {"num": 30, "den": 1},
            "cfr": True, "progressive": True, "sample_aspect_num": 1, "sample_aspect_den": 1,
            "rotation_degrees": 0, "colorspace": 709, "video_stream_index": 0,
            "audio_stream_index": 1 if audio else None}


def edl_fixture(sources=None, ranges=None):
    sources = sources or [source_fixture()]
    ranges = ranges or [(sources[0]["id"], 30, 60)]
    by_id = {source["id"]: source for source in sources}
    clips, cursor = [], 0
    for index, (identifier, start, end) in enumerate(ranges):
        source = by_id[identifier]
        rate = subtitles.frame_rate(source["fps"])
        clips.append({"id": f"clip{index}", "source_id": identifier, "source_in_frame": start,
                      "source_out_frame": end, "timeline_in_frame": cursor,
                      "timeline_out_frame": cursor + end - start,
                      "provenance": {"candidate_id": f"candidate{index}", "candidate_document_sha256": "c" * 64,
                                     "original_source_id": identifier, "original_source_sha256": source["sha256"],
                                     "original_start_seconds": float(Fraction(start, 1) / rate),
                                     "original_end_seconds": float(Fraction(end, 1) / rate),
                                     "original_in_frame": start, "original_out_frame": end,
                                     "original_selection_index": index, "source_sentences": []}})
        cursor += end - start
    return {"schema_version": "0.1.0", "artifact_kind": "edit_decision_list",
            "profile": {key: deepcopy(sources[0][key]) for key in ("width", "height", "fps")},
            "sources": sources, "clips": clips, "duration_frames": cursor}


def analysis_fixture(source=None, words=None, *, duration=None):
    source = source or source_fixture()
    duration = duration if duration is not None else float(Fraction(source["total_frames"], 1) / subtitles.frame_rate(source["fps"]))
    words = words if words is not None else [(0.7, 1.2, "原词一", 0), (1.2, 1.5, " 原词二。", 0),
                                            (1.8, 2.3, "下一句", 1), (2.3, 2.5, "相邻场景", 1)]
    has_audio = source["audio_stream_index"] is not None
    rows, groups = [], {}
    if has_audio:
        for index, (start, end, text, group) in enumerate(words):
            rows.append({"id": f"w{index}", "start": start, "end": end, "text": text, "probability": None})
            groups.setdefault(group, []).append(rows[-1])
    sentences = [{"id": f"s{number}", "start": min(row["start"] for row in members),
                  "end": max(row["end"] for row in members), "text": "".join(row["text"] for row in members),
                  "word_ids": [row["id"] for row in members]} for number, members in groups.items()]
    analyzers = {name: {"status": "ok" if name == "shots" or has_audio else "no_audio", "tool": "unit-fixture",
                        "version": "1", "model": None, "parameters": {}} for name in ("shots", "vad", "asr")}
    return {"schema_version": "0.1.0", "result_kind": "synthetic_example",
            "media": {"source_sha256": "d" * 64, "normalized_sha256": source["sha256"],
                      "duration_seconds": duration, "time_reference": "normalized_video", "has_audio": has_audio},
            "analyzers": analyzers, "shots": [{"start": 0, "end": duration}],
            "speech": [{"start": 0, "end": duration}] if has_audio else [], "silence": [],
            "words": rows, "sentences": sentences}


class SubtitleTests(unittest.TestCase):
    def test_schema_deterministic_original_words_and_clipped_evidence(self):
        schema = json.loads((subtitles.ROOT / "schemas/subtitles.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        edl, analyses = edl_fixture(), {"source": analysis_fixture()}
        before = deepcopy((edl, analyses))
        plan = subtitles.build_subtitles(edl, analyses)
        subtitles.validate_subtitles(plan, edl, analyses)
        self.assertEqual((edl, analyses), before)
        self.assertEqual(plan, subtitles.build_subtitles(edl, analyses))
        self.assertEqual(plan["edl_sha256"], subtitles._digest(edl))
        self.assertEqual(plan["sources"][0]["analysis_sha256"], subtitles._digest(analyses["source"]))
        self.assertEqual([cue["word_ids"] for cue in plan["cues"]], [["w0", "w1"], ["w2"]])
        self.assertEqual(plan["clip_reports"][0]["excluded_word_ids"], ["w3"])
        self.assertEqual(plan["clip_reports"][0]["clipped_word_ids"], ["w0", "w2"])
        first, last = plan["cues"][0]["word_mappings"][0], plan["cues"][-1]["word_mappings"][0]
        self.assertEqual((first["original_start_seconds"], first["source_start_seconds"]), (.7, 1.0))
        self.assertTrue(first["clipped_start"] and last["clipped_end"])
        self.assertEqual(last["source_end_seconds"], 2.0)
        self.assertEqual(plan["cues"][0]["text"], "原词一 原词二。")
        self.assertEqual(plan["sources"][0]["analysis_result_kind"], "synthetic_example")

    def test_repeated_reordered_ranges_and_multisource_never_borrow_words(self):
        first, second = source_fixture(), source_fixture("other")
        edl = edl_fixture([first, second], [("other", 60, 90), ("source", 0, 30), ("source", 0, 30)])
        analyses = {"source": analysis_fixture(first, [(0, .5, "源一", 0), (1, 1.5, "未选", 1)]),
                    "other": analysis_fixture(second, [(2, 2.5, "源二", 0)])}
        plan = subtitles.build_subtitles(edl, analyses)
        self.assertEqual([cue["text"] for cue in plan["cues"]], ["源二", "源一", "源一"])
        self.assertEqual([cue["timeline_in_frame"] for cue in plan["cues"]], [0, 30, 60])
        self.assertEqual([cue["source_id"] for cue in plan["cues"]], ["other", "source", "source"])
        self.assertEqual(plan["clip_reports"][1]["excluded_word_ids"], ["w1"])
        self.assertEqual(plan["cues"][1]["word_ids"], plan["cues"][2]["word_ids"])

    def test_half_open_touching_words_excluded_and_tiny_word_expanded_inside_clip(self):
        analysis = analysis_fixture(words=[(.9, 1, "边界前", 0), (1.001, 1.002, "极短", 1), (2, 2.1, "边界后", 2)])
        plan = subtitles.build_subtitles(edl_fixture(), {"source": analysis})
        self.assertEqual(plan["cues"][0]["word_ids"], ["w1"])
        self.assertEqual((plan["cues"][0]["timeline_in_frame"], plan["cues"][0]["timeline_out_frame"]), (0, 1))
        self.assertEqual(plan["clip_reports"][0]["short_expanded_word_ids"], ["w1"])
        self.assertIn("00:00:00,000 --> 00:00:00,033", subtitles.to_srt(plan))

    def test_overlapping_words_merge_and_exceptions_are_reported_without_loss(self):
        source = source_fixture()
        edl = edl_fixture([source], [("source", 0, 240)])
        analysis = analysis_fixture(source, [(0, 6, "长" * 50, 0), (1, 6.5, "重叠", 1), (7, 7.5, "后句", 2)])
        plan = subtitles.build_subtitles(edl, {"source": analysis})
        self.assertEqual(plan["cues"][0]["word_ids"], ["w0", "w1"])
        self.assertEqual(plan["cues"][1]["word_ids"], ["w2"])
        self.assertEqual(plan["clip_reports"][0]["overlapping_word_ids"], ["w1"])
        self.assertTrue({"overlap_merged", "duration_limit_exception", "line_width_exception"}.issubset(plan["cues"][0]["flags"]))
        self.assertEqual(plan["cues"][0]["text"].count("\n"), 1)
        self.assertEqual(plan["cues"][0]["text"].replace("\n", ""), "长" * 50 + "重叠")

    def test_sentence_pause_duration_and_readable_two_line_grouping(self):
        source = source_fixture()
        words = [(0, .4, "甲" * 18, 0), (.4, .8, "乙" * 18, 0), (.8, 1, "丙", 0),
                 (1.5, 2, "停顿后", 0), (2, 2.3, "新句", 1), (6.4, 6.7, "时长后", 1)]
        plan = subtitles.build_subtitles(edl_fixture([source], [("source", 0, 240)]), {"source": analysis_fixture(source, words)})
        self.assertEqual([cue["word_ids"] for cue in plan["cues"]], [["w0"], ["w1", "w2"], ["w3"], ["w4"], ["w5"]])
        self.assertEqual(plan["cues"][1]["text"], "乙" * 18 + "\n丙")
        self.assertIn("orphan_tail_rebalanced", plan["cues"][1]["flags"])
        for cue in plan["cues"]:
            self.assertLessEqual(len(cue["text"].splitlines()), 2)
            self.assertLessEqual(cue["timeline_out_frame"] - cue["timeline_in_frame"], 120)

    def test_short_greedy_tail_merges_with_explicit_target_exception_without_retiming(self):
        source = source_fixture()
        words = [(0, 1, "甲", 0), (1, 2, "乙", 0), (2, 3, "丙", 0),
                 (3, 3.95, "丁", 0), (3.95, 4.15, "尾", 0)]
        edl, analysis = edl_fixture([source], [("source", 0, 150)]), analysis_fixture(source, words)
        plan = subtitles.build_subtitles(edl, {"source": analysis})
        self.assertEqual([cue["word_ids"] for cue in plan["cues"]], [["w0", "w1", "w2", "w3", "w4"]])
        self.assertTrue({"orphan_tail_merged", "duration_target_exception"}.issubset(plan["cues"][0]["flags"]))
        self.assertNotIn("duration_limit_exception", plan["cues"][0]["flags"])
        self.assertLessEqual(Fraction(plan["cues"][0]["timeline_out_frame"], 30), Fraction("4.5"))
        previous_end = None
        expected = []
        for word in analysis["words"]:
            expected.append(subtitles._mapping(word, edl["clips"][0], Fraction(30), previous_end))
            previous_end = Fraction(str(word["end"]))
        self.assertEqual(plan["cues"][0]["word_mappings"], expected)
        subtitles.validate_subtitles(plan, edl, {"source": analysis})

    def test_tail_rebalanced_without_crossing_sentence_pause_or_clip(self):
        source = source_fixture()
        words = [(0, 1, "甲", 0), (1, 2, "乙", 0), (2, 3, "丙", 0),
                 (3, 3.99, "丁", 0), (4.4, 4.6, "尾", 0)]
        edl = edl_fixture([source], [("source", 0, 150), ("source", 0, 150)])
        plan = subtitles.build_subtitles(edl, {"source": analysis_fixture(source, words)})
        self.assertEqual([cue["word_ids"] for cue in plan["cues"]],
                         [["w0", "w1", "w2"], ["w3", "w4"], ["w0", "w1", "w2"], ["w3", "w4"]])
        for report in plan["clip_reports"]:
            self.assertEqual(report["selected_word_ids"], [f"w{index}" for index in range(5)])
        self.assertTrue(all("orphan_tail_rebalanced" in cue["flags"] for cue in plan["cues"]))
        for tail in ((3.99, 4.2, "尾", 1), (4.5, 4.7, "尾", 0)):
            with self.subTest(tail=tail):
                boundary = subtitles.build_subtitles(edl_fixture([source], [("source", 0, 150)]),
                                                       {"source": analysis_fixture(source, words[:-1] + [tail])})
                self.assertEqual(boundary["cues"][-1]["word_ids"], ["w4"])
                self.assertIn("short_cue_exception", boundary["cues"][-1]["flags"])
                self.assertNotIn("orphan_tail_merged", boundary["cues"][-1]["flags"])

    def test_rational_mapping_and_inward_srt_ass_quantization(self):
        source = source_fixture(fps={"num": 30000, "den": 1001})
        edl = edl_fixture([source], [("source", 30, 60)])
        analysis = analysis_fixture(source, [(1.001, 1.3346666666666667, "有理帧率", 0)])
        plan = subtitles.build_subtitles(edl, {"source": analysis})
        self.assertEqual((plan["cues"][0]["timeline_in_frame"], plan["cues"][0]["timeline_out_frame"]), (0, 10))
        self.assertIn("00:00:00,000 --> 00:00:00,333", subtitles.to_srt(plan))
        ass = subtitles.to_ass(plan)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:00.33", ass)
        self.assertIn("Dialogue: 1,0:00:00.00,0:00:01.01,Brand", ass)

    def test_no_audio_and_no_selected_words_have_explicit_reports_and_brand(self):
        source = source_fixture(audio=False)
        plan = subtitles.build_subtitles(edl_fixture([source]), {"source": analysis_fixture(source)})
        self.assertEqual(plan["clip_reports"][0]["status"], "no_audio")
        self.assertEqual(plan["cues"], [])
        self.assertEqual(subtitles.to_srt(plan), "")
        self.assertIn("AI辅助剪辑", subtitles.to_ass(plan))
        plan = subtitles.build_subtitles(edl_fixture(), {"source": analysis_fixture(words=[(3, 4, "范围外", 0)])})
        self.assertEqual(plan["clip_reports"][0]["status"], "no_words_in_clip")
        self.assertEqual(plan["clip_reports"][0]["excluded_word_ids"], ["w0"])

    def test_ass_control_injection_disabled_font_literal_and_profile_styles(self):
        text = r"原词{\pos(1,1)\fs80}\N结束" + "\n下一行"
        plan = subtitles.build_subtitles(edl_fixture(), {"source": analysis_fixture(words=[(1, 1.5, text, 0)])})
        ass = subtitles.to_ass(plan, font_name="Noto Sans CJK SC")
        self.assertNotIn(r"{\pos", ass)
        self.assertNotIn(r"\fs80", ass)
        self.assertIn("｛＼pos(1,1)＼fs80｝＼N", ass)
        self.assertEqual(plan["cues"][0]["word_mappings"][0]["text"], text)
        self.assertIn("PlayResX: 160", ass)
        self.assertIn("PlayResY: 90", ass)
        self.assertIn("Style: Brand,Noto Sans CJK SC,8", ass)
        self.assertIn("Dialogue: 1,", ass)
        for font in ("bad,name", "bad\nStyle: injected", r"bad\name", "{bad}", ""):
            with self.subTest(font=font), self.assertRaises(ValueError):
                subtitles.to_ass(plan, font_name=font)

    def test_srt_markup_is_literal_but_original_word_text_is_preserved(self):
        raw = "原词<i>斜体</i><font color='red'>原样</font>"
        plan = subtitles.build_subtitles(edl_fixture(), {"source": analysis_fixture(words=[(1, 1.5, raw, 0)])})
        srt = subtitles.to_srt(plan)
        body = "\n".join(srt.splitlines()[2:])
        self.assertNotIn("<", body)
        self.assertNotIn(">", body)
        self.assertIn("＜i＞斜体＜/i＞", srt)
        self.assertEqual(plan["cues"][0]["word_mappings"][0]["text"], raw)

    def test_nonempty_ass_interval_must_cover_a_selected_cfr_frame(self):
        for fps in ({"num": 30, "den": 1}, {"num": 30000, "den": 1001}):
            source = source_fixture(fps=fps)
            rate = subtitles.frame_rate(fps)
            for first in (1, 2):
                with self.subTest(fps=fps, first=first):
                    words = [(float(Fraction(first, 1) / rate), float(Fraction(first + 1, 1) / rate), "字", 0)]
                    plan = subtitles.build_subtitles(edl_fixture([source], [("source", 0, 4)]),
                                                     {"source": analysis_fixture(source, words)})
                    cue = plan["cues"][0]
                    self.assertLess(*subtitles._event_ticks(cue, fps, 100))
                    self.assertEqual(plan["clip_reports"][0]["ass_unrepresentable_cue_ids"], [cue["id"]])
                    with self.assertRaisesRegex(ValueError, "centisecond.*clip"):
                        subtitles.to_ass(plan)
            # A one-frame interval beginning at an exactly representable PTS is visible.
            visible = subtitles.build_subtitles(edl_fixture([source], [("source", 0, 4)]),
                                                  {"source": analysis_fixture(source, [(0, float(1 / rate), "字", 0)])})
            self.assertEqual(visible["clip_reports"][0]["ass_unrepresentable_cue_ids"], [])
            self.assertIn("Dialogue: 0,", subtitles.to_ass(visible))

    def test_ass_unrepresentable_one_frame_reports_evidence_and_fails_clearly(self):
        source = source_fixture(fps={"num": 120, "den": 1})
        plan = subtitles.build_subtitles(edl_fixture([source], [("source", 0, 1)]),
                                         {"source": analysis_fixture(source, [(0, .001, "短词", 0)])})
        self.assertEqual(plan["clip_reports"][0]["ass_unrepresentable_cue_ids"], [plan["cues"][0]["id"]])
        self.assertIn("00:00:00,008", subtitles.to_srt(plan))
        with self.assertRaisesRegex(ValueError, "cue_0001_0001.*centisecond.*clip"):
            subtitles.to_ass(plan)

    def test_input_analysis_binding_complete_schema_and_one_frame_duration_tolerance(self):
        edl = edl_fixture()
        original = analysis_fixture()
        for mutation in (lambda data: data["media"].update(normalized_sha256="f" * 64),
                         lambda data: data["media"].update(time_reference="original_video"),
                         lambda data: data["media"].update(has_audio=False),
                         lambda data: data["words"][0].update(id="w1"),
                         lambda data: data["sentences"][0].update(word_ids=["unknown"]),
                         lambda data: data.update(unexpected=True),
                         lambda data: data["words"][0].update(end=float("nan"))):
            analysis = deepcopy(original)
            mutation(analysis)
            with self.assertRaises(ValueError):
                subtitles.build_subtitles(edl, {"source": analysis})
        for analyses in ({}, {"source": original, "extra": original}):
            with self.assertRaises(ValueError):
                subtitles.build_subtitles(edl, analyses)
        subtitles.build_subtitles(edl, {"source": analysis_fixture(duration=10.03)})
        with self.assertRaisesRegex(ValueError, "duration"):
            subtitles.build_subtitles(edl, {"source": analysis_fixture(duration=10.04)})
        tail = analysis_fixture(words=[(9.99, 10.02, "源尾词", 0)], duration=10.03)
        plan = subtitles.build_subtitles(edl_fixture(ranges=[("source", 299, 300)]), {"source": tail})
        self.assertTrue(plan["cues"][0]["word_mappings"][0]["clipped_end"])

    def test_strict_reconstruction_and_standalone_serializer_validation(self):
        edl, analyses = edl_fixture(), {"source": analysis_fixture()}
        plan = subtitles.build_subtitles(edl, analyses)
        for mutation in (lambda data: data.update(edl_sha256="f" * 64),
                         lambda data: data["sources"][0].update(analysis_sha256="f" * 64),
                         lambda data: data["cues"][0]["flags"].append("duration_limit_exception"),
                         lambda data: data["clip_reports"][0]["excluded_word_ids"].clear()):
            changed = deepcopy(plan)
            mutation(changed)
            with self.assertRaisesRegex(ValueError, "reconstruction"):
                subtitles.validate_subtitles(changed, edl, analyses)
        for mutation in (lambda data: data.pop("profile"),
                         lambda data: data["cues"][0].update(timeline_out_frame=31),
                         lambda data: data["cues"][0].update(timeline_in_frame=0.0),
                         lambda data: data["cues"][0].update(text="篡改文本"),
                         lambda data: data.update(duration_frames=True),
                         lambda data: data["profile"]["fps"].update(num=25)):
            changed = deepcopy(plan)
            mutation(changed)
            for serializer in (subtitles.to_srt, subtitles.to_ass):
                with self.assertRaises(ValueError):
                    serializer(changed)

    def test_packaged_schema_root_used_for_both_plan_and_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schemas").mkdir()
            for name in ("analysis.schema.json", "subtitles.schema.json"):
                (root / "schemas" / name).write_text((subtitles.ROOT / "schemas" / name).read_text(encoding="utf-8"), encoding="utf-8")
            with patch.object(subtitles, "ROOT", root):
                plan = subtitles.build_subtitles(edl_fixture(), {"source": analysis_fixture()})
                self.assertIn("原词", subtitles.to_srt(plan))
                (root / "schemas/subtitles.schema.json").unlink()
                with self.assertRaises(FileNotFoundError):
                    subtitles.to_ass(plan)


if __name__ == "__main__":
    unittest.main()
