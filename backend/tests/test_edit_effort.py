"""Net-edit metrics preserve source identity and do not pretend to measure labor."""

from copy import deepcopy
from fractions import Fraction
import json
import unittest

from clipforge.editing.edl import candidates_to_edl, create_portrait_edl, validate_edl
from clipforge.editing.effort import compare_edit_effort


def fixture(count=4, fps=None):
    source = {"id": "source-1", "ref": "normalized.mp4", "sha256": "a" * 64,
              "total_frames": 10000, "width": 960, "height": 600,
              "fps": fps or {"num": 30, "den": 1}, "cfr": True, "progressive": True,
              "sample_aspect_num": 1, "sample_aspect_den": 1, "rotation_degrees": 0,
              "colorspace": 709, "video_stream_index": 0, "audio_stream_index": 1}
    candidates = {
        "media": {"normalized_sha256": source["sha256"], "has_audio": True,
                  "time_reference": "normalized_video", "duration_seconds": 150},
        "candidates": [{"id": f"c{i}", "start": 10 + 20 * i, "end": 20 + 20 * i,
                        "source_sentences": [f"s{i}"]} for i in range(count)],
    }
    return candidates_to_edl(candidates, [f"c{i}" for i in range(count)], source)


def timeline(edl):
    cursor = 0
    for clip in edl["clips"]:
        clip["timeline_in_frame"] = cursor
        cursor += clip["source_out_frame"] - clip["source_in_frame"]
        clip["timeline_out_frame"] = cursor
    edl["duration_frames"] = cursor
    validate_edl(edl)
    return edl


class EditEffortTests(unittest.TestCase):
    def test_identical_snapshots_are_zero_and_inputs_are_not_mutated(self):
        baseline, final = fixture(), fixture()
        before = deepcopy((baseline, final))
        report = compare_edit_effort(baseline, final)
        for name in ("net_boundary_adjustment_seconds", "adjusted_clip_count", "added_clip_count",
                     "removed_clip_count", "minimum_moved_clip_count",
                     "added_clip_boundary_adjustment_seconds"):
            self.assertEqual(report[name], 0)
        self.assertEqual(report["net_boundary_adjustment_seconds_exact"], {"num": 0, "den": 1})
        self.assertEqual((baseline, final), before)
        json.loads(json.dumps(report, allow_nan=False))
        report["common_clips"][0]["provenance"]["source_sentences"].append("new")
        self.assertEqual((baseline, final), before)

    def test_opposing_endpoint_shifts_do_not_cancel(self):
        baseline, final = fixture(), fixture()
        final["clips"][0]["source_in_frame"] += 30
        final["clips"][0]["source_out_frame"] -= 30
        report = compare_edit_effort(baseline, timeline(final))
        self.assertEqual(report["net_boundary_adjustment_seconds"], 2)
        self.assertEqual(report["adjusted_clip_count"], 1)
        first = report["common_clips"][0]
        self.assertEqual((first["in_delta_seconds"], first["out_delta_seconds"]), (1, -1))
        self.assertEqual((first["in_delta_frames"], first["out_delta_frames"]), (30, -30))

    def test_restoring_an_edit_reports_zero_instead_of_cumulative_operations(self):
        baseline, final = fixture(), fixture()
        final["clips"][0]["source_in_frame"] += 15
        timeline(final)
        self.assertEqual(compare_edit_effort(baseline, final)["net_boundary_adjustment_seconds"], 0.5)
        final["clips"][0]["source_in_frame"] -= 15
        report = compare_edit_effort(baseline, timeline(final))
        self.assertEqual(report["net_boundary_adjustment_seconds"], 0)
        self.assertEqual(report["adjusted_clip_count"], 0)
        self.assertTrue(any("not cumulative" in item for item in report["limitations"]))

    def test_fractional_fps_accumulates_without_intermediate_rounding(self):
        fps = {"num": 30000, "den": 1001}
        baseline, final = fixture(fps=fps), fixture(fps=fps)
        final["clips"][0]["source_in_frame"] += 1
        final["clips"][1]["source_out_frame"] -= 2
        final["clips"][2]["source_in_frame"] += 1
        report = compare_edit_effort(baseline, timeline(final))
        expected = Fraction(4 * 1001, 30000)
        self.assertEqual(report["net_boundary_adjustment_seconds_exact"],
                         {"num": expected.numerator, "den": expected.denominator})
        self.assertEqual(report["net_boundary_adjustment_seconds"], float(expected))
        self.assertEqual(report["adjusted_clip_count"], 3)
        self.assertEqual(report["common_clips"][1]["out_delta_seconds_exact"],
                         {"num": -1001, "den": 15000})

    def test_addition_and_deletion_do_not_report_reordering(self):
        all_clips = fixture()
        baseline, final = deepcopy(all_clips), deepcopy(all_clips)
        baseline["clips"] = [baseline["clips"][0], baseline["clips"][2]]
        final["clips"] = final["clips"][1:]
        report = compare_edit_effort(timeline(baseline), timeline(final))
        self.assertEqual(report["common_clip_count"], 1)
        self.assertEqual(report["added_clip_count"], 2)
        self.assertEqual(report["removed_clip_count"], 1)
        self.assertEqual(report["minimum_moved_clip_count"], 0)
        self.assertEqual(report["net_boundary_adjustment_seconds"], 0)

    def test_added_clip_trim_is_reported_separately_against_provenance(self):
        all_clips = fixture()
        baseline, final = deepcopy(all_clips), deepcopy(all_clips)
        baseline["clips"] = baseline["clips"][:1]
        final["clips"] = final["clips"][:2]
        final["clips"][1]["source_in_frame"] += 15
        final["clips"][1]["source_out_frame"] -= 30
        report = compare_edit_effort(timeline(baseline), timeline(final))
        self.assertEqual(report["added_clip_count"], 1)
        self.assertEqual(report["net_boundary_adjustment_seconds"], 0)
        self.assertEqual(report["adjusted_clip_count"], 0)
        self.assertEqual(report["added_clip_boundary_adjustment_seconds"], 1.5)
        self.assertEqual(report["added_clip_boundary_adjustment_seconds_exact"], {"num": 3, "den": 2})
        self.assertEqual(report["added_clips"][0]["in_delta_frames"], 15)

    def test_reordering_uses_lcs_minimum_not_number_of_displaced_indices(self):
        baseline, final = fixture(), fixture()
        final["clips"] = final["clips"][1:] + final["clips"][:1]
        report = compare_edit_effort(baseline, timeline(final))
        self.assertEqual(report["minimum_moved_clip_count"], 1)
        self.assertEqual(report["adjusted_clip_count"], 0)
        final["clips"].reverse()
        # The resulting order is c0,c3,c2,c1; its LCS has length two.
        self.assertEqual(compare_edit_effort(baseline, timeline(final))["minimum_moved_clip_count"], 2)

    def test_reorder_with_insertions_and_deletions_counts_only_common_clips(self):
        original = fixture(5)
        baseline, final = deepcopy(original), deepcopy(original)
        baseline["clips"] = baseline["clips"][:4]
        final["clips"] = [final["clips"][3], final["clips"][4], final["clips"][1], final["clips"][2]]
        report = compare_edit_effort(timeline(baseline), timeline(final))
        self.assertEqual((report["added_clip_count"], report["removed_clip_count"]), (1, 1))
        self.assertEqual(report["minimum_moved_clip_count"], 1)

    def test_same_ranges_with_different_candidate_document_are_not_nearest_matches(self):
        baseline, final = fixture(), fixture()
        for clip in final["clips"]:
            clip["provenance"]["candidate_document_sha256"] = "b" * 64
        report = compare_edit_effort(baseline, final)
        self.assertEqual(report["common_clip_count"], 0)
        self.assertEqual((report["added_clip_count"], report["removed_clip_count"]), (4, 4))
        self.assertEqual(report["minimum_moved_clip_count"], 0)

    def test_clip_display_id_changes_do_not_change_identity(self):
        baseline, final = fixture(), fixture()
        for index, clip in enumerate(final["clips"]):
            clip["id"] = f"different-id-{index}"
        report = compare_edit_effort(baseline, final)
        self.assertEqual(report["common_clip_count"], 4)
        self.assertEqual(report["added_clip_count"], 0)
        self.assertEqual(report["net_boundary_adjustment_seconds"], 0)

    def test_changed_provenance_for_common_identity_is_rejected(self):
        for field, value in (("source_sentences", ["different-sentence"]),
                             ("original_selection_index", 100),
                             ("original_start_seconds", 11)):
            with self.subTest(field=field):
                baseline, final = fixture(), fixture()
                provenance = final["clips"][0]["provenance"]
                provenance[field] = value
                if field == "original_start_seconds":
                    provenance["original_in_frame"] = 330
                validate_edl(final)  # Individually valid does not make provenance interchangeable.
                with self.assertRaisesRegex(ValueError, "entire baseline provenance"):
                    compare_edit_effort(baseline, final)

    def test_different_media_or_source_metadata_is_rejected(self):
        for field, value in (("sha256", "b" * 64), ("ref", "other-path.mp4"),
                             ("total_frames", 11000)):
            with self.subTest(field=field):
                baseline, final = fixture(), fixture()
                final["sources"][0][field] = value
                if field == "sha256":
                    for clip in final["clips"]:
                        clip["provenance"]["original_source_sha256"] = value
                validate_edl(final)
                with self.assertRaisesRegex(ValueError, "identical source declarations"):
                    compare_edit_effort(baseline, final)
        with self.assertRaisesRegex(ValueError, "native fps"):
            compare_edit_effort(fixture(), fixture(fps={"num": 60, "den": 1}))

    def test_unchanged_portrait_conversion_has_zero_boundary_effort(self):
        baseline = fixture()
        final = create_portrait_edl(baseline)
        report = compare_edit_effort(baseline, final)
        self.assertEqual(report["net_boundary_adjustment_seconds"], 0)
        self.assertEqual(report["adjusted_clip_count"], 0)
        self.assertEqual(report["minimum_moved_clip_count"], 0)
        self.assertNotEqual(report["baseline_edl_sha256"], report["final_edl_sha256"])

    def test_each_input_is_validated_before_comparison(self):
        for invalid_side in (0, 1):
            values = [fixture(), fixture()]
            values[invalid_side]["clips"][0]["source_in_frame"] = True
            with self.subTest(invalid_side=invalid_side), self.assertRaises(ValueError):
                compare_edit_effort(*values)


if __name__ == "__main__":
    unittest.main()
