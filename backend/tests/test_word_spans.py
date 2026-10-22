"""Invariant tests for research spans and scoped human event-example evidence."""
from copy import deepcopy
from fractions import Fraction
import unittest

from clipforge.editing.word_spans import (
    content_hash, propose_word_span, verify_proposal,
    extend_with_reviewed_example, verify_reviewed_example,
)


def fixture(times=None, fps=None, total_frames=300):
    times = times or [(0.10, 0.40), (0.60, 0.90), (1.10, 1.40), (1.60, 1.90)]
    fps = fps or {"num": 30, "den": 1}
    duration = float(Fraction(total_frames * fps["den"], fps["num"]))
    words = [{"id": f"w{i}", "start": start, "end": end,
              "text": text, "probability": .99}
             for i, ((start, end), text) in enumerate(zip(times, ["旧内容", "其实", "问答", "后续"]))]
    analysis = {"schema_version": "0.1.0", "result_kind": "measured",
                "media": {"normalized_sha256": "a" * 64, "source_sha256": "b" * 64,
                          "duration_seconds": duration, "time_reference": "normalized_video", "has_audio": True},
                "words": words,
                "sentences": [{"id": "s0", "start": min(a for a, _ in times),
                               "end": max(b for _, b in times), "text": "旧内容其实问答后续",
                               "word_ids": [row["id"] for row in words]}],
                "speech": [{"start": .05, "end": duration - .05}], "analyzers": {}}
    measured = {"profile": {"fps": deepcopy(fps)},
                "source": {"id": "normalized", "sha256": "a" * 64, "fps": deepcopy(fps),
                           "total_frames": total_frames, "cfr": True, "audio_stream_index": 1}}
    return analysis, measured


class FrameProposalTests(unittest.TestCase):
    def test_30fps_exact_edges_and_no_unselected_text_or_original_mutation(self):
        a, m = fixture()
        original = deepcopy((a, m))
        p = propose_word_span(a, m, "w1", "w2")
        self.assertEqual((18, 42), (p["frame_span"]["source_in_frame"], p["frame_span"]["source_out_frame"]))
        self.assertEqual(p["source_words"], ["w1", "w2"])
        self.assertEqual(p["source_sentences"], ["s0"])
        self.assertEqual(p["text"], "其实问答")
        self.assertNotIn("旧内容", p["text"])
        self.assertNotIn("后续", p["text"])
        self.assertFalse(p["sentence_ownership"][0]["all_sentence_words_selected"])
        self.assertEqual((a, m), original)
        self.assertEqual(p, propose_word_span(a, m, "w1", "w2"))
        self.assertTrue(verify_proposal(p, a, m))
        for key in ("semantic_completeness_verified", "lexical_boundary_verified",
                    "audible_word_alignment_verified", "automatically_recommendable", "production_candidate"):
            self.assertIs(p[key], False)

    def test_fractional_30000_1001fps_edges_are_rational_and_outward(self):
        a, m = fixture(fps={"num": 30000, "den": 1001})
        p = propose_word_span(a, m, "w1", "w2")
        self.assertEqual((17, 42), (p["frame_span"]["source_in_frame"], p["frame_span"]["source_out_frame"]))
        self.assertEqual(p["frame_span"]["start_seconds_exact"], {"num": 17017, "den": 30000})
        self.assertEqual(p["frame_span"]["end_seconds_exact"], {"num": 7007, "den": 5000})
        self.assertLessEqual(Fraction(17 * 1001, 30000), Fraction("0.60"))
        self.assertGreaterEqual(Fraction(42 * 1001, 30000), Fraction("1.40"))

    def test_nonframe_start_gap_has_no_safe_frame(self):
        a, m = fixture([(0.1, .615), (.620, .9), (1.1, 1.4), (1.6, 1.9)])
        with self.assertRaisesRegex(ValueError, "no legal integer frame"):
            propose_word_span(a, m, "w1", "w2")

    def test_nonframe_end_gap_has_no_safe_frame(self):
        a, m = fixture([(0.1, .4), (.6, .9), (1.1, 1.405), (1.410, 1.9)])
        with self.assertRaisesRegex(ValueError, "no legal integer frame"):
            propose_word_span(a, m, "w1", "w2")

    def test_long_earlier_excluded_word_overlap_cannot_hide_behind_nearest_word(self):
        a, m = fixture([(0.1, 1.5), (.3, .4), (.6, .9), (1.6, 1.9)])
        with self.assertRaisesRegex(ValueError, "overlapping"):
            propose_word_span(a, m, "w2", "w2")

    def test_selected_long_word_overlapping_next_excluded_word_cannot_be_split(self):
        a, m = fixture([(0.1, .4), (.6, 1.5), (1.1, 1.4), (1.6, 1.9)])
        with self.assertRaisesRegex(ValueError, "overlapping"):
            propose_word_span(a, m, "w1", "w1")
        p = propose_word_span(a, m, "w1", "w2")
        self.assertEqual(p["frame_span"]["source_out_frame"], 45)

    def test_source_direct_or_complete_manifest_preserves_its_evidence_identity(self):
        a, m = fixture()
        p = propose_word_span(a, m["source"], "w0", "w3")
        self.assertEqual(p["source_sentences"], ["s0"])
        self.assertTrue(p["sentence_ownership"][0]["all_sentence_words_selected"])
        self.assertEqual(p["measured_media_sha256"], content_hash(m["source"]))
        self.assertTrue(verify_proposal(p, a, m["source"]))
        with self.assertRaises(ValueError):
            verify_proposal(p, a, m)

    def test_hash_is_bound_to_entire_original_analysis_and_measured_evidence(self):
        a, m = fixture()
        p = propose_word_span(a, m, "w1", "w2")
        body = deepcopy(p)
        digest = body.pop("proposal_sha256")
        self.assertEqual(digest, content_hash(body))
        a["words"][0]["probability"] = .98
        q = propose_word_span(a, m, "w1", "w2")
        self.assertNotEqual(p["proposal_sha256"], q["proposal_sha256"])
        with self.assertRaises(ValueError):
            verify_proposal(p, a, m)

    def test_recomputed_hash_cannot_launder_changed_text_or_frames(self):
        a, m = fixture()
        for field, value in (("text", "伪造内容"), ("source_words", ["w0", "w1", "w2"])):
            p = propose_word_span(a, m, "w1", "w2")
            p[field] = value
            p.pop("proposal_sha256")
            p["proposal_sha256"] = content_hash(p)
            with self.assertRaises(ValueError):
                verify_proposal(p, a, m)
        p = propose_word_span(a, m, "w1", "w2")
        p["frame_span"]["source_out_frame"] -= 1
        with self.assertRaises(ValueError):
            verify_proposal(p, a, m)

    def test_wrong_or_reversed_ids_are_rejected(self):
        a, m = fixture()
        for first, last in (("bad", "w2"), ("w1", "bad"), ("w2", "w1"), (True, "w2")):
            with self.subTest(first=first, last=last), self.assertRaises(ValueError):
                propose_word_span(a, m, first, last)

    def test_bad_fps_are_rejected(self):
        for fps in ({"num": True, "den": 1}, {"num": 30.0, "den": 1}, {"num": 30, "den": 0},
                    {"num": 60, "den": 2}, {"num": 121, "den": 1}, {"num": float("nan"), "den": 1}):
            a, m = fixture()
            m["source"]["fps"] = fps
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                propose_word_span(a, m, "w1", "w2")

    def test_identity_frame_duration_audio_and_cfr_mismatches_are_rejected(self):
        for key, value in (("sha256", "c" * 64), ("total_frames", True), ("total_frames", 100),
                           ("cfr", False), ("audio_stream_index", None)):
            a, m = fixture()
            m["source"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                propose_word_span(a, m, "w1", "w2")
        a, m = fixture()
        m["profile"]["fps"]["num"] = 25
        with self.assertRaises(ValueError):
            propose_word_span(a, m, "w1", "w2")

    def test_bad_word_numbers_ranges_and_sentence_ownership_are_rejected(self):
        for value in (True, float("nan"), float("inf"), -1, 1.5):
            a, m = fixture()
            a["words"][1]["start"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                propose_word_span(a, m, "w1", "w2")
        a, m = fixture()
        a["sentences"][0]["word_ids"] = ["w0", "w1", "w1", "w3"]
        with self.assertRaises(ValueError):
            propose_word_span(a, m, "w1", "w2")
        a, m = fixture()
        a["words"][1]["id"] = "w0"
        with self.assertRaises(ValueError):
            propose_word_span(a, m, "w0", "w2")

    def test_coverage_gap_touching_end_stays_unverified_even_when_speech_detected(self):
        a, m = fixture([(0.1, .4), (.6, .9), (1.1, 1.4), (6.0, 6.5)])
        p = propose_word_span(a, m, "w1", "w2")
        risks = p["coverage_audit"]["risks"]
        self.assertEqual(len(risks), 1)
        self.assertTrue(risks[0]["suspect_detector_disagreement"])
        self.assertFalse(p["coverage_audit"]["coverage_verified"])
        self.assertFalse(p["automatically_recommendable"])
        a.pop("speech")
        q = propose_word_span(a, m, "w1", "w2")
        self.assertEqual(q["coverage_audit"]["available_detectors"], [])
        self.assertFalse(q["coverage_audit"]["risks"][0]["suspect_detector_disagreement"])
        self.assertFalse(q["coverage_audit"]["coverage_verified"])

    def test_words_beyond_measured_frame_endpoint_are_rejected_not_clamped(self):
        a, m = fixture([(0.1, .4), (.6, .9), (1.1, 1.4), (9.99, 10.01)])
        a["media"]["duration_seconds"] = 10.01
        with self.assertRaisesRegex(ValueError, "measured video frame range|overlapping"):
            propose_word_span(a, m, "w0", "w3")


def evidence(analysis, start=1.4, end=6.015):
    return {"schema_version": "0.1.0-research.1", "artifact_kind": "reviewed_non_dialogue_example",
            "analysis_sha256": content_hash(analysis),
            "normalized_sha256": analysis["media"]["normalized_sha256"],
            "scope": {"start_seconds": start, "end_seconds": end},
            "adjudication": {"reviewer": "user", "method": "listening", "no_dialogue": True,
                             "relation": "same_event_example",
                             "verbatim_user_statement": "测试夹具：只有音乐，没有讲话，是当前事件的音乐示例。"}}


def inputs(fps=None):
    a, m = fixture([(0.1, .4), (.6, .9), (1.1, 1.4), (6.015, 6.5)], fps=fps)
    p = propose_word_span(a, m, "w1", "w2")
    return a, m, p, evidence(a)


class ReviewedMusicSpanTests(unittest.TestCase):
    def test_preserves_music_example_but_never_invents_words_or_reclassifies_raw_coverage(self):
        a, m, base, note = inputs()
        original = deepcopy((a, m, base, note))
        p = extend_with_reviewed_example(base, a, m, note)
        self.assertEqual(p["frame_span"]["source_out_frame"], 180)
        self.assertEqual(p["source_words"], ["w1", "w2"])
        self.assertEqual(p["text"], base["text"])
        self.assertEqual(p["source_sentences"], base["source_sentences"])
        self.assertEqual(p["coverage_audit"], base["coverage_audit"])
        self.assertTrue(p["coverage_audit"]["risks"][0]["suspect_detector_disagreement"])
        self.assertEqual(p["annotation_sha256"], content_hash(note))
        self.assertEqual((a, m, base, note), original)
        self.assertEqual(p, extend_with_reviewed_example(base, a, m, note))
        self.assertTrue(verify_reviewed_example(p, a, m, note))
        for key in ("semantic_completeness_verified", "audible_word_alignment_verified",
                    "lexical_boundary_verified", "automatically_recommendable", "production_candidate"):
            self.assertIs(p[key], False)

    def test_tail_quantization_is_reported_exactly_instead_of_claiming_full_scope(self):
        a, m, base, note = inputs()
        p = extend_with_reviewed_example(base, a, m, note)
        music = p["included_non_dialogue_spans"][0]
        self.assertEqual(music["omitted_tail_seconds_exact"], {"num": 3, "den": 200})
        self.assertFalse(music["full_annotated_scope_retained"])
        self.assertFalse(music["lyrics_inferred"])
        self.assertFalse(music["transcript_words_added"])
        self.assertEqual(music["retained_end_seconds_exact"], {"num": 6, "den": 1})

    def test_actual_30fps_boundaries_keep_word_end_music_and_stop_before_next_na(self):
        a, m = fixture([(0.1, .4), (34.2, 34.64), (122.11, 122.41), (129.54, 129.7)], total_frames=5584)
        base = propose_word_span(a, m, "w1", "w2")
        note = evidence(a, 122.41, 129.54)
        p = extend_with_reviewed_example(base, a, m, note)
        self.assertEqual(p["frame_span"]["source_in_frame"], 1026)
        self.assertEqual(p["frame_span"]["source_out_frame"], 3886)
        self.assertEqual(p["frame_span"]["duration_frames"], 2860)
        self.assertAlmostEqual(p["frame_span"]["duration_seconds"], 95.33333333333333)
        self.assertFalse(p["frame_span"]["within_current_15_90_second_limit"])
        music = p["included_non_dialogue_spans"][0]
        self.assertEqual(music["fully_inside_example_in_frame"], 3673)
        self.assertEqual(music["omitted_tail_seconds_exact"], {"num": 1, "den": 150})
        self.assertTrue(music["first_frame_may_also_contain_selected_word_tail"])
        self.assertLess(Fraction(3886, 30), Fraction("129.54"))
        self.assertGreater(Fraction(3887, 30), Fraction("129.54"))

    def test_fractional_fps_uses_floor_not_nearest_or_ceil(self):
        a, m, base, note = inputs(fps={"num": 30000, "den": 1001})
        p = extend_with_reviewed_example(base, a, m, note)
        out = p["frame_span"]["source_out_frame"]
        self.assertEqual(out, 180)
        self.assertEqual(p["frame_span"]["end_seconds_exact"], {"num": 3003, "den": 500})
        self.assertEqual(p["included_non_dialogue_spans"][0]["omitted_tail_seconds_exact"], {"num": 9, "den": 1000})

    def test_exact_frame_endpoint_may_report_zero_loss(self):
        a, m = fixture([(0.1, .4), (.6, .9), (1.1, 1.4), (6, 6.5)])
        base = propose_word_span(a, m, "w1", "w2")
        p = extend_with_reviewed_example(base, a, m, evidence(a, 1.4, 6))
        self.assertTrue(p["included_non_dialogue_spans"][0]["full_annotated_scope_retained"])
        self.assertEqual(p["included_non_dialogue_spans"][0]["omitted_tail_seconds_exact"], {"num": 0, "den": 1})

    def test_wrong_analysis_or_media_identity_rejected(self):
        for field in ("analysis_sha256", "normalized_sha256"):
            a, m, base, note = inputs()
            note[field] = "c" * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity"):
                extend_with_reviewed_example(base, a, m, note)

    def test_requires_explicit_user_listening_no_dialogue_and_same_event_example(self):
        for field, value in (("reviewer", "model"), ("method", "VAD"), ("no_dialogue", False),
                             ("no_dialogue", 1), ("relation", "unrelated_music"),
                             ("verbatim_user_statement", "")):
            a, m, base, note = inputs()
            note["adjudication"][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "explicit user"):
                extend_with_reviewed_example(base, a, m, note)

    def test_wider_or_partial_annotation_cannot_be_used_to_extend_this_gap(self):
        for start, end in ((1.3, 6.015), (1.5, 6.015), (1.4, 6.0), (1.4, 6.1), (6.015, 1.4)):
            a, m, base, note = inputs()
            note["scope"] = {"start_seconds": start, "end_seconds": end}
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                extend_with_reviewed_example(base, a, m, note)

    def test_bool_nan_and_unknown_annotation_fields_rejected(self):
        for value in (True, float("nan"), float("inf")):
            a, m, base, note = inputs()
            note["scope"]["start_seconds"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                extend_with_reviewed_example(base, a, m, note)
        a, m, base, note = inputs()
        note["automatic_event_pass"] = True
        with self.assertRaises(ValueError):
            extend_with_reviewed_example(base, a, m, note)

    def test_full_reconstruction_rejects_tampering_even_with_recomputed_hash(self):
        a, m, base, note = inputs()
        for change in ("word", "frame", "adjudication", "coverage"):
            p = extend_with_reviewed_example(base, a, m, note)
            if change == "word":
                p["text"] += "歌词"
            elif change == "frame":
                p["frame_span"]["source_out_frame"] += 1
            elif change == "adjudication":
                p["human_adjudication"]["other_coverage_scopes_adjudicated"] = True
            else:
                p["coverage_audit"]["coverage_verified"] = True
            p.pop("proposal_sha256")
            p["proposal_sha256"] = content_hash(p)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_reviewed_example(p, a, m, note)

    def test_changed_user_statement_changes_proposal_hash_and_breaks_old_verification(self):
        a, m, base, note = inputs()
        p = extend_with_reviewed_example(base, a, m, note)
        note["adjudication"]["verbatim_user_statement"] += "追加说明。"
        q = extend_with_reviewed_example(base, a, m, note)
        self.assertNotEqual(p["proposal_sha256"], q["proposal_sha256"])
        with self.assertRaises(ValueError):
            verify_reviewed_example(p, a, m, note)

    def test_base_must_be_a_verified_original_proposal_and_extension_is_not_chainable(self):
        a, m, base, note = inputs()
        base["text"] += "伪造"
        with self.assertRaises(ValueError):
            extend_with_reviewed_example(base, a, m, note)
        a, m, base, note = inputs()
        p = extend_with_reviewed_example(base, a, m, note)
        with self.assertRaises(ValueError):
            extend_with_reviewed_example(p, a, m, note)

    def test_zero_additional_frame_gap_and_missing_next_word_rejected(self):
        a, m = fixture([(0.1, .4), (.6, .9), (1.1, 1.4), (1.41, 1.9)])
        base = propose_word_span(a, m, "w1", "w2")
        with self.assertRaisesRegex(ValueError, "no additional"):
            extend_with_reviewed_example(base, a, m, evidence(a, 1.4, 1.41))
        a, m, base, note = inputs()
        base = propose_word_span(a, m, "w1", "w3")
        with self.assertRaisesRegex(ValueError, "next original word"):
            extend_with_reviewed_example(base, a, m, evidence(a, 6.5, 7))


if __name__ == "__main__":
    unittest.main()
