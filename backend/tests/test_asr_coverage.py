import copy
import unittest

from clipforge.analysis.coverage import audit_word_coverage


def analysis(words, speech, duration=20, gate=None):
    value = {"media": {"duration_seconds": duration}, "words": words, "speech": speech,
             "sentences": [], "analyzers": {"asr": {"parameters": {"speech_gate": gate}}}}
    return value


def word(name, start, end):
    return {"id": name, "start": start, "end": end, "text": "measured"}


class ASRCoverageTests(unittest.TestCase):
    def test_hole_inside_one_sentence_is_detected_without_changing_words(self):
        value = analysis([word("left", 1, 2), word("right", 13, 14)],
                         [{"start": 1, "end": 14}])
        value["sentences"] = [{"start": 1, "end": 14, "word_ids": ["left", "right"]}]
        before = copy.deepcopy(value)
        report = audit_word_coverage(value)
        self.assertEqual(value, before)
        self.assertEqual(report["suspect_interval_count"], 1)
        interval = report["suspect_intervals"][0]
        self.assertEqual((interval["start"], interval["end"]), (2, 13))
        self.assertEqual((interval["left_word_id"], interval["right_word_id"]), ("left", "right"))
        self.assertEqual(interval["position"], "internal")

    def test_true_silence_and_small_padding_are_not_flagged(self):
        value = analysis([word("left", 1, 2), word("right", 13, 14)],
                         [{"start": 1, "end": 2.2}, {"start": 12.8, "end": 14}])
        self.assertEqual(audit_word_coverage(value)["suspect_intervals"], [])
        self.assertEqual(audit_word_coverage(value)["status"], "clear")

    def test_detector_intervals_are_union_not_double_counted(self):
        value = analysis([word("a", 0, 1), word("b", 10, 11)],
                         [{"start": 2, "end": 4}, {"start": 3, "end": 4.5}],
                         gate={"speech_intervals": [{"start": 2, "end": 4.5}]})
        # Both detectors and overlapping intervals detect only 2.5 seconds.
        self.assertEqual(audit_word_coverage(value)["suspect_intervals"], [])
        value["speech"].append({"start": 6, "end": 7})
        interval = audit_word_coverage(value)["suspect_intervals"][0]
        self.assertEqual(interval["speech_seconds"], 3.5)
        self.assertEqual(interval["detectors"]["silero_vad"]["speech_seconds"], 2.5)

    def test_prefix_suffix_and_empty_asr_remain_unverified_not_fake_transcript(self):
        value = analysis([word("a", 7, 8)], [{"start": 0, "end": 20}])
        report = audit_word_coverage(value)
        self.assertEqual([x["position"] for x in report["suspect_intervals"]], ["prefix", "suffix"])
        value["words"] = []
        interval = audit_word_coverage(value)["suspect_intervals"][0]
        self.assertEqual((interval["start"], interval["end"], interval["position"]), (0, 20, "whole_media"))
        self.assertIsNone(interval["left_word_id"])
        self.assertFalse(audit_word_coverage(value)["words_changed"])

    def test_only_one_detector_is_sufficient_but_absent_detector_is_unobserved(self):
        value = analysis([word("a", 0, 1), word("b", 10, 11)], [],
                         gate={"speech_intervals": [{"start": 1, "end": 10}]})
        self.assertEqual(audit_word_coverage(value)["status"], "suspect_coverage")
        del value["speech"]
        value["analyzers"] = {}
        self.assertEqual(audit_word_coverage(value)["status"], "unobserved")

    def test_gap_threshold_is_strict_and_overlapping_words_cover_the_union(self):
        value = analysis([word("a", 0, 2), word("b", 1, 4), word("c", 7, 20)],
                         [{"start": 0, "end": 20}])
        self.assertEqual(audit_word_coverage(value)["suspect_intervals"], [])
        value["words"][2]["start"] = 7.001
        self.assertEqual(audit_word_coverage(value)["suspect_interval_count"], 1)

    def test_invalid_detector_and_word_timings_are_rejected(self):
        for start, end in ((float("nan"), 4), (2, float("inf")), (3, 2), (-1, 4), (1, 21)):
            for field in ("words", "speech"):
                value = analysis([], [])
                value[field] = [{"start": start, "end": end}]
                with self.subTest(start=start, end=end, field=field), self.assertRaises(ValueError):
                    audit_word_coverage(value)


if __name__ == "__main__":
    unittest.main()
