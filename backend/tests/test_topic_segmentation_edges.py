"""Segmentation evidence, ambiguity, scale invariance and explicit resource limits."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from test_candidates import fixture
from clipforge.decision import topic_segments
from clipforge.decision.topic_segments import (
    TopicOptions, generate_topic_candidates, segment_topics,
)


def analysis_with_texts(texts, seconds=8):
    data = fixture([(i * seconds, (i + 1) * seconds) for i in range(len(texts))])
    for word, sentence, text in zip(data["words"], data["sentences"], texts):
        word["text"] = sentence["text"] = text
    return data


def token(index):
    return "marker" + chr(97 + index // 26) + chr(97 + index % 26)


class TopicSegmentationEdgeTests(unittest.TestCase):
    def report(self, texts, **options):
        return segment_topics(analysis_with_texts(texts), TopicOptions(window_units=1, **options))

    def test_flat_valley_selects_middle_earlier_tie_reproducibly(self):
        texts = ["alpha", "alpha", "beta", "gamma", "delta", "omega", "omega"]
        report = self.report(texts)
        self.assertEqual(report, self.report(texts))
        self.assertEqual([b["similarity"] for b in report["boundaries"]], [1, 0, 0, 0, 0, 1])
        accepted = [b for b in report["boundaries"] if b["decision"] == "accepted_valley"]
        self.assertEqual([b["after_unit"] for b in accepted], [2])
        self.assertEqual(accepted[0]["plateau_after_units"], [1, 2, 3, 4])
        self.assertEqual(accepted[0]["depth"], 1)
        self.assertEqual(accepted[0]["raw_depth"], 2)
        self.assertEqual([(s["first_unit"], s["last_unit"]) for s in report["segments"]],
                         [(0, 2), (3, 6)])

    def test_shallow_valley_is_reported_but_rejected(self):
        shared = " ".join(token(i) for i in range(50))
        original, changed = shared + " originalextra", shared + " changedextra"
        report = self.report([original, original, changed, original, original])
        rejected = [b for b in report["boundaries"] if b["reasons"] == ["below_min_depth"]]
        self.assertEqual(len(rejected), 1)
        self.assertGreater(rejected[0]["depth"], 0)
        self.assertLess(rejected[0]["depth"], report["options"]["min_depth"])
        self.assertGreater(rejected[0]["raw_depth"], 0)
        self.assertEqual(rejected[0]["decision"], "rejected")
        self.assertEqual(len(report["segments"]), 1)

    def test_ranked_equal_valleys_obey_minimum_segment_distance(self):
        report = self.report(["alpha"] * 3 + ["beta"] * 2 + ["gamma"] * 3,
                             min_segment_units=3)
        self.assertEqual(report["boundaries"][2]["decision"], "accepted_valley")
        self.assertEqual(report["boundaries"][4]["decision"], "rejected")
        self.assertEqual(report["boundaries"][4]["reasons"], ["min_segment_units_conflict"])
        self.assertEqual(report["boundaries"][2]["depth"], report["boundaries"][4]["depth"])
        self.assertEqual([(s["first_unit"], s["last_unit"]) for s in report["segments"]],
                         [(0, 2), (3, 7)])

    def test_unbracketed_first_and_last_low_scores_do_not_force_splits(self):
        report = self.report(["alpha", "beta", "beta", "gamma"])
        self.assertEqual([b["similarity"] for b in report["boundaries"]], [0, 1, 0])
        self.assertTrue(all(b["depth"] is None for b in report["boundaries"]))
        self.assertEqual(len(report["segments"]), 1)

    def test_unknown_windows_break_peak_evidence_instead_of_becoming_zero(self):
        report = self.report(["alpha", "alpha", "beta", "！！！", "gamma", "delta", "delta"])
        self.assertEqual([b["similarity"] for b in report["boundaries"]], [1, 0, None, None, 0, 1])
        self.assertEqual([b["after_unit"] for b in report["boundaries"]
                          if b["reasons"] == ["uninformative_window"]], [2, 3])
        self.assertTrue(all(b["depth"] is None for b in report["boundaries"]))
        self.assertEqual(len(report["segments"]), 1)

    def test_relative_valley_decisions_are_invariant_to_curve_scale(self):
        texts = ["alpha beta", "alpha beta", "alpha gamma", "alpha gamma",
                 "delta epsilon", "delta epsilon", "delta epsilon"]
        analysis = analysis_with_texts(texts)
        options = TopicOptions(window_units=1)
        original = segment_topics(analysis, options)
        similarity = topic_segments._similarity

        def scaled(left, right, idf):
            value = similarity(left, right, idf)
            return None if value is None else value * 0.01

        # Transform the real algorithm's entire measured curve. This tests the
        # decision statistic, without substituting invented per-gap outcomes.
        with patch.object(topic_segments, "_similarity", side_effect=scaled):
            smaller = segment_topics(analysis, options)
        self.assertEqual([(b["after_unit"], b["decision"], b["reasons"])
                          for b in original["boundaries"]],
                         [(b["after_unit"], b["decision"], b["reasons"])
                          for b in smaller["boundaries"]])
        for a, b in zip(original["boundaries"], smaller["boundaries"]):
            self.assertEqual(a["depth"], b["depth"])
            if a["raw_depth"] is not None:
                self.assertAlmostEqual(a["raw_depth"] * 0.01, b["raw_depth"], places=10)
        self.assertTrue(any(b["depth"] is not None for b in original["boundaries"]))
        self.assertEqual(original["segments"], smaller["segments"])

    def test_long_flat_valley_saved_evidence_grows_linearly(self):
        sizes = []
        for count in (100, 200):
            texts = ["alpha", "alpha"] + [token(i) for i in range(count)] + ["omega", "omega"]
            report = self.report(texts)
            self.assertEqual(sum(bool(b["plateau_after_units"]) for b in report["boundaries"]), 1)
            sizes.append(len(json.dumps(report, ensure_ascii=False).encode("utf-8")))
        self.assertLess(sizes[1], 3 * sizes[0])

    def test_sentence_count_limit_is_inclusive_and_failure_does_not_truncate(self):
        allowed = analysis_with_texts(["alpha", "alpha"])
        forbidden = analysis_with_texts(["alpha", "alpha", "alpha"])
        before = deepcopy(forbidden)
        with patch.object(topic_segments, "MAX_SENTENCES", 2):
            self.assertEqual(len(segment_topics(allowed)["units"]), 2)
            with self.assertRaisesRegex(ValueError, "句子数量超过固定上限"):
                segment_topics(forbidden)
        self.assertEqual(forbidden, before)

    def test_text_limit_is_inclusive_and_failure_does_not_truncate(self):
        analysis = analysis_with_texts(["alpha", "beta"])
        before = deepcopy(analysis)
        with patch.object(topic_segments, "MAX_TEXT_CHARACTERS", 9):
            self.assertEqual(len(segment_topics(analysis)["units"]), 2)
        with patch.object(topic_segments, "MAX_TEXT_CHARACTERS", 8):
            with self.assertRaisesRegex(ValueError, "句子文字超过固定上限"):
                segment_topics(analysis)
        self.assertEqual(analysis, before)

    def test_serialized_byte_limit_is_inclusive_and_failure_does_not_truncate(self):
        analysis = analysis_with_texts(["苹果", "香蕉"])
        before = deepcopy(analysis)
        size = len(json.dumps(analysis, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))
        with patch.object(topic_segments, "MAX_ANALYSIS_BYTES", size):
            self.assertEqual(len(segment_topics(analysis)["units"]), 2)
        with patch.object(topic_segments, "MAX_ANALYSIS_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, "分析数据超过固定上限"):
                segment_topics(analysis)
        self.assertEqual(analysis, before)

    def test_candidate_limit_fails_without_partial_result_or_legacy_fallback(self):
        analysis = analysis_with_texts(["alpha"] * 2 + ["beta"] * 2)
        before = deepcopy(analysis)
        with self.assertRaisesRegex(ValueError, "max_candidates"):
            generate_topic_candidates(analysis, TopicOptions(window_units=1, max_candidates=1))
        self.assertEqual(analysis, before)


if __name__ == "__main__":
    unittest.main()
