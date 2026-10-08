"""句子窗口的边界、来源校验和离线命令行回归。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from clipforge.decision.candidates import ROOT, WindowOptions, generate_candidates
from clipforge.decision.validation import validate_candidates


def fixture(intervals):
    data = json.loads((ROOT / "docs/samples/analysis.example.json").read_text(encoding="utf-8"))
    duration = max([end for _, end in intervals] + [1]) + 1
    data["media"]["duration_seconds"] = duration
    data["shots"] = [{"start": 0, "end": duration}]
    data["speech"] = [{"start": 0, "end": duration}]
    data["silence"] = []
    data["words"], data["sentences"] = [], []
    for i, (start, end) in enumerate(intervals):
        data["words"].append({"id": f"w{i}", "start": start, "end": end,
                              "text": f"句子{i}", "probability": 0.9})
        data["sentences"].append({"id": f"s{i}", "start": start, "end": end,
                                  "text": f"句子{i}", "word_ids": [f"w{i}"]})
    return data


class CandidateTests(unittest.TestCase):
    def run_case(self, intervals, options=None):
        data = fixture(intervals)
        result = generate_candidates(data, options)
        validate_candidates(result, data)
        return data, result

    def test_every_eligible_consecutive_window(self):
        _, result = self.run_case([(0, 10), (10, 20), (20, 30)])
        self.assertEqual([(c["start"], c["end"]) for c in result["candidates"]],
                         [(0, 20), (0, 30), (10, 30)])
        self.assertTrue(all(c["score"] is None and c["scorer"] is None
                            and c["reasons"] == [] for c in result["candidates"]))

    def test_minimum_and_maximum_are_inclusive(self):
        for duration in (15, 90):
            _, result = self.run_case([(0.1, duration + 0.1)])
            self.assertEqual(result["candidate_count"], 1)

    def test_short_clip_returns_empty_without_padding(self):
        _, result = self.run_case([(1, 10)])
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["warnings"], ["no_eligible_window"])

    def test_long_sentence_skipped_without_blocking_later_windows(self):
        _, result = self.run_case([(0, 91), (92, 110)])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["source_sentences"], ["s1"])
        self.assertIn("overlong_sentence_unit_skipped", result["warnings"])

    def test_long_pause_splits_windows_and_is_configurable(self):
        data, result = self.run_case([(0, 8), (12, 20)])
        self.assertEqual(result["candidate_count"], 0)
        result = generate_candidates(data, WindowOptions(max_gap_seconds=4))
        validate_candidates(result, data)
        self.assertEqual(result["candidate_count"], 1)

    def test_overlap_group_cannot_be_partially_selected(self):
        _, result = self.run_case([(0, 20), (10, 25), (24, 30), (30, 45)])
        self.assertEqual([c["source_sentences"] for c in result["candidates"]],
                         [["s0", "s1", "s2"], ["s0", "s1", "s2", "s3"], ["s3"]])

    def test_nested_overlap_does_not_end_window_early(self):
        _, result = self.run_case([(0, 30), (10, 15), (20, 25)])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["end"], 30)

    def test_silent_and_no_audio_inputs(self):
        data, result = self.run_case([])
        self.assertIn("no_sentences", result["warnings"])
        data["media"]["has_audio"] = False
        data["speech"] = []
        for key in ("vad", "asr"):
            data["analyzers"][key]["status"] = "no_audio"
        result = generate_candidates(data)
        validate_candidates(result, data)
        self.assertEqual(result["candidate_count"], 0)

    def test_reproducible_and_does_not_modify_analysis(self):
        data = fixture([(0, 20)])
        before = deepcopy(data)
        first = generate_candidates(data)
        self.assertEqual(first, generate_candidates(data))
        self.assertEqual(data, before)
        first["media"]["duration_seconds"] = 1
        self.assertEqual(data, before)

    def test_candidate_limit_fails_instead_of_silently_truncating(self):
        with self.assertRaisesRegex(ValueError, "max_candidates"):
            generate_candidates(fixture([(0, 20), (20, 40)]), WindowOptions(max_candidates=1))

    def test_invalid_options(self):
        for args in ({"min_seconds": 14}, {"max_seconds": 91},
                     {"min_seconds": 50, "max_seconds": 30}, {"max_gap_seconds": -1},
                     {"max_gap_seconds": float("nan")}, {"max_candidates": True},
                     {"max_candidates": 0}, {"min_seconds": True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                WindowOptions(**args)

    def test_invalid_analysis_rejected(self):
        data = fixture([(0, 20)])
        data["sentences"][0]["word_ids"] = ["unknown"]
        with self.assertRaises(ValueError):
            generate_candidates(data)

    def test_corruption_rejected(self):
        data, baseline = self.run_case([(0, 10), (10, 20), (20, 30)])
        mutations = [lambda d: d.update(analysis_sha256="0" * 64),
                     lambda d: d.update(candidate_count=99),
                     lambda d: d["candidates"][0].update(start=1),
                     lambda d: d["candidates"][0].update(duration_seconds=21),
                     lambda d: d["candidates"][0].update(score=88),
                     lambda d: d["candidates"][0].update(text="changed"),
                     lambda d: d["candidates"][0].update(source_sentences=["s0", "s2"]),
                     lambda d: d["candidates"][0].update(source_sentences=["unknown"]),
                     lambda d: d["candidates"][1].update(**d["candidates"][0]),
                     lambda d: d["media"].update(has_audio=False)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = deepcopy(baseline)
                mutation(result)
                with self.assertRaises(ValueError):
                    validate_candidates(result, data)

    def test_cli_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "analysis.json"
            output = Path(directory) / "candidates.json"
            source.write_text(json.dumps(fixture([(0, 20)])), encoding="utf-8")
            command = [sys.executable, "-m", "clipforge.decision.candidates", str(source),
                       "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            validate_candidates(json.loads(output.read_text(encoding="utf-8")),
                                json.loads(source.read_text(encoding="utf-8")))
            original = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), original)
            command[-1] = str(source)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
