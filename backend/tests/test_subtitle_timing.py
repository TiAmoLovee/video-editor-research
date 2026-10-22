"""Explicit display timing revisions never rewrite the saved ASR evidence."""

from copy import deepcopy
import hashlib
import unittest

from jsonschema import Draft202012Validator

from backend.tests.test_subtitles import analysis_fixture, edl_fixture, source_fixture
from clipforge.editing import edl, subtitles


def fixture(*, portrait=False):
    source = source_fixture()
    project = edl_fixture([source], [("source", 0, 90), ("source", 120, 180)])
    if portrait:
        project = edl.create_portrait_edl(project, mode="fit_pad")
    analyses = {"source": analysis_fixture(source, [(.2, .6, "首", 0), (.6, 1, "句", 0),
                                                   (2.2, 2.5, "下", 1), (2.5, 2.8, "句", 1),
                                                   (4.2, 4.8, "后段", 2)])}
    base = subtitles.build_subtitles(project, analyses)
    return project, analyses, base


def edit(cue, start=None, end=None):
    return {"cue_id": cue["id"],
            "timeline_in_frame": cue["timeline_in_frame"] if start is None else start,
            "timeline_out_frame": cue["timeline_out_frame"] if end is None else end}


class SubtitleTimingTests(unittest.TestCase):
    def test_correction_changes_only_named_display_ranges_and_binds_original_plan(self):
        project, analyses, base = fixture()
        before = deepcopy((project, analyses, base))
        requested = [edit(base["cues"][1], start=68), edit(base["cues"][0], start=15, end=31)]
        original_requests = deepcopy(requested)
        timed = subtitles.retime_subtitles(base, project, analyses, requested, evidence_id="human-sync-start-001")
        self.assertEqual((project, analyses, base), before)
        self.assertEqual(requested, original_requests)
        self.assertEqual(timed["schema_version"], "0.2.0")
        self.assertEqual(timed["timing_revision"], {"base_plan_sha256": subtitles._digest(base),
                                                   "evidence_id": "human-sync-start-001",
                                                   "edits": list(reversed(requested))})
        recovered = deepcopy(timed)
        recovered.pop("timing_revision")
        recovered["schema_version"] = "0.1.0"
        for old, new in zip(base["cues"], recovered["cues"], strict=True):
            for key in ("timeline_in_frame", "timeline_out_frame"):
                new[key] = old[key]
        self.assertEqual(recovered, base)
        self.assertEqual(timed["cues"][0]["word_mappings"], base["cues"][0]["word_mappings"])
        self.assertEqual(timed["cues"][0]["word_mappings"][0]["timeline_in_frame"], 6)
        self.assertEqual(timed["cues"][0]["timeline_in_frame"], 15)
        subtitles.validate_subtitles(timed, project, analyses)
        self.assertIn("00:00:00,500 --> 00:00:01,033", subtitles.to_srt(timed))
        self.assertIn("Dialogue: 0,0:00:00.50,0:00:01.03,", subtitles.to_ass(timed))

    def test_invalid_unknown_duplicate_empty_noop_and_nonstrict_changes_rejected(self):
        project, analyses, base = fixture()
        valid = edit(base["cues"][0], start=15)
        malformed = ([], None, {}, [valid] * 101, [valid, valid],
                     [{**valid, "cue_id": "unknown"}], [edit(base["cues"][0])],
                     [{**valid, "timeline_in_frame": 15.0}],
                     [{**valid, "timeline_in_frame": True}],
                     [{**valid, "timeline_in_frame": "15"}],
                     [{**valid, "source_start_seconds": .5}],
                     [{"cue_id": valid["cue_id"], "timeline_in_frame": 15}])
        for edits in malformed:
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                subtitles.retime_subtitles(base, project, analyses, edits, evidence_id="evidence")

    def test_display_cannot_cross_cut_overlap_another_cue_or_become_empty(self):
        project, analyses, base = fixture()
        first, second, third = base["cues"]
        invalid = ([edit(first, start=-1)], [edit(first, start=30, end=30)],
                   [edit(first, start=31, end=30)], [edit(first, end=91)],
                   [edit(second, start=20)], [edit(third, start=89)],
                   [edit(first, end=67)], [edit(second, end=1000000000000)])
        for edits in invalid:
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                subtitles.retime_subtitles(base, project, analyses, edits, evidence_id="evidence")

    def test_safe_evidence_id_original_base_and_source_binding_are_required(self):
        project, analyses, base = fixture()
        request = [edit(base["cues"][0], start=15)]
        for evidence in (None, "", " ", " leading", "trailing ", "line\nbreak", "\x00", "\x7f", "x" * 201):
            with self.subTest(evidence=repr(evidence)), self.assertRaises(ValueError):
                subtitles.retime_subtitles(base, project, analyses, request, evidence_id=evidence)
        timed = subtitles.retime_subtitles(base, project, analyses, request, evidence_id="local-human-001")
        with self.assertRaisesRegex(ValueError, "original 0.1.0"):
            subtitles.retime_subtitles(timed, project, analyses, request, evidence_id="chained")
        wrong_base = deepcopy(base)
        wrong_base["edl_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            subtitles.retime_subtitles(wrong_base, project, analyses, request, evidence_id="evidence")
        wrong_analyses = deepcopy(analyses)
        wrong_analyses["source"]["words"][0]["text"] = "改"
        with self.assertRaises(ValueError):
            subtitles.validate_subtitles(timed, project, wrong_analyses)

    def test_changed_binding_evidence_extra_fields_and_unrecorded_display_changes_rejected(self):
        project, analyses, base = fixture()
        timed = subtitles.retime_subtitles(base, project, analyses, [edit(base["cues"][0], start=15)],
                                           evidence_id="evidence")
        changes = (lambda value: value["timing_revision"].update(base_plan_sha256="0" * 64),
                   lambda value: value["timing_revision"].update(extra=True),
                   lambda value: value["timing_revision"]["edits"][0].update(extra=True),
                   lambda value: value["timing_revision"].update(evidence_id=""),
                   lambda value: value["cues"][0].update(timeline_in_frame=16),
                   lambda value: value["cues"][1].update(timeline_in_frame=68),
                   lambda value: value["cues"][0]["word_mappings"][0].update(original_start_seconds=.1),
                   lambda value: value["cues"][0].update(text="伪造字幕"),
                   lambda value: value.update(extra=True))
        for index, change in enumerate(changes):
            changed = deepcopy(timed)
            change(changed)
            for serialize in (subtitles.to_srt, subtitles.to_ass):
                with self.subTest(change=index, serializer=serialize.__name__), self.assertRaises(ValueError):
                    serialize(changed)
            with self.subTest(change=index, serializer="full"), self.assertRaises(ValueError):
                subtitles.validate_subtitles(changed, project, analyses)

    def test_self_consistent_changed_original_evidence_still_requires_real_analysis_rebuild(self):
        project, analyses, base = fixture()
        forged = deepcopy(base)
        forged["cues"][0]["word_mappings"][0]["original_start_seconds"] = .1
        # A caller can recompute a hash; that never authenticates source facts.
        timed = subtitles._retimed_plan(forged, [edit(forged["cues"][0], start=15)], "evidence")
        subtitles._validate_plan(timed)
        with self.assertRaisesRegex(ValueError, "reconstruction"):
            subtitles.validate_subtitles(timed, project, analyses)

    def test_one_frame_is_allowed_only_when_serialization_keeps_a_visible_frame(self):
        project, analyses, base = fixture()
        timed = subtitles.retime_subtitles(base, project, analyses, [edit(base["cues"][0], start=15, end=16)],
                                           evidence_id="one-frame")
        self.assertIn("0:00:00.50,0:00:00.53", subtitles.to_ass(timed))
        # 16/30 -> 17/30 quantizes inward between frame PTS; rejecting it
        # prevents an apparently nonempty EDL cue that ASS would never show.
        with self.assertRaisesRegex(ValueError, "represented"):
            subtitles.retime_subtitles(base, project, analyses, [edit(base["cues"][0], start=16, end=17)],
                                       evidence_id="no-visible-frame")

    def test_fit_portrait_policy_and_mapping_are_preserved(self):
        project, analyses, base = fixture(portrait=True)
        timed = subtitles.retime_subtitles(base, project, analyses, [edit(base["cues"][0], start=15)],
                                           evidence_id="portrait")
        self.assertEqual(timed["policy"], subtitles.PORTRAIT_POLICY)
        self.assertEqual(timed["profile"], project["profile"])
        self.assertIn("PlayResX: 1080\nPlayResY: 1920", subtitles.to_ass(timed))
        subtitles.validate_subtitles(timed, project, analyses)

    def test_new_schema_is_local_and_old_schema_bytes_remain_unchanged(self):
        original = subtitles.ROOT / "schemas/subtitles.schema.json"
        self.assertEqual(hashlib.sha256(original.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                         "1bf60ccdd441b2a9e1534ba0436358ce448f69b379c1eab64ee288c3218476de")
        schema = subtitles._schema("subtitles-v0.2.schema.json")
        Draft202012Validator.check_schema(schema)
        project, analyses, base = fixture()
        timed = subtitles.retime_subtitles(base, project, analyses, [edit(base["cues"][0], start=15)],
                                           evidence_id="schema-check")
        Draft202012Validator(schema).validate(timed)
        self.assertEqual(subtitles.build_subtitles(project, analyses), base)
        self.assertEqual(base["schema_version"], "0.1.0")
        self.assertNotIn("timing_revision", base)


if __name__ == "__main__":
    unittest.main()
