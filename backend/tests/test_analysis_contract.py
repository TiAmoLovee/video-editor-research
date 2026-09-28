"""数据契约检查，不运行模型，也不把手工示例作为精度证据。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from clipforge.analysis.validation import validate_analysis

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas/analysis.schema.json"
EXAMPLE_PATH = ROOT / "docs/samples/analysis.example.json"


class AnalysisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    def setUp(self):
        self.data = deepcopy(self.example)

    def validate(self):
        validate_analysis(self.data, self.schema)

    def test_schema_is_valid_draft_202012(self):
        Draft202012Validator.check_schema(self.schema)

    def test_synthetic_example_passes(self):
        self.validate()
        self.assertEqual(self.data["result_kind"], "synthetic_example")

    def test_missing_field_wrong_type_unknown_field_and_version_fail(self):
        mutations = [lambda d: d.pop("words"),
                     lambda d: d.update(schema_version="9.0.0"),
                     lambda d: d.update(speech="wrong"),
                     lambda d: d.update(typo=True),
                     lambda d: d["media"].update(has_audio=1),
                     lambda d: d["media"].update(source_sha256="not-a-hash"),
                     lambda d: d["words"][0].update(probability=1.1),
                     lambda d: d["words"][0].update(text=" ")]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.data = deepcopy(self.example)
                mutate(self.data)
                with self.assertRaises(ValueError):
                    self.validate()

    def test_invalid_interval_bounds_fail(self):
        for start, end in [(-1, 2), (2, 2), (3, 2), (1, 11)]:
            with self.subTest(start=start, end=end):
                self.data = deepcopy(self.example)
                self.data["words"][0].update(start=start, end=end)
                with self.assertRaisesRegex(ValueError, "start|minimum"):
                    self.validate()

    def test_nonfinite_values_fail_including_parameters(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.data["analyzers"]["asr"]["parameters"]["threshold"] = value
                with self.assertRaisesRegex(ValueError, "NaN/Infinity"):
                    self.validate()

    def test_shots_must_cover_video_without_gaps_or_overlap(self):
        for start in (3.9, 4.1):
            with self.subTest(start=start):
                self.data = deepcopy(self.example)
                self.data["shots"][1]["start"] = start
                with self.assertRaises(ValueError):
                    self.validate()
        self.data = deepcopy(self.example)
        self.data["shots"].pop()
        with self.assertRaisesRegex(ValueError, "完整时长"):
            self.validate()

    def test_speech_and_silence_are_a_partition(self):
        for end in (0.9, 1.1):
            with self.subTest(end=end):
                self.data = deepcopy(self.example)
                self.data["silence"][0]["end"] = end
                with self.assertRaisesRegex(ValueError, "空隙或重叠"):
                    self.validate()

    def test_unsorted_words_fail(self):
        self.data["words"].reverse()
        with self.assertRaisesRegex(ValueError, "排序"):
            self.validate()

    def test_duplicate_ids_fail(self):
        for name in ("words", "sentences"):
            with self.subTest(name=name):
                self.data = deepcopy(self.example)
                self.data[name][1]["id"] = self.data[name][0]["id"]
                with self.assertRaisesRegex(ValueError, "id 重复"):
                    self.validate()

    def test_sentence_unknown_reference_fails(self):
        self.data["sentences"][0]["word_ids"][0] = "missing"
        with self.assertRaisesRegex(ValueError, "不存在的词"):
            self.validate()

    def test_sentence_time_must_match_referenced_words(self):
        self.data["sentences"][0]["end"] = 3.1
        with self.assertRaisesRegex(ValueError, "句子边界"):
            self.validate()

    def test_sentence_grouping_cannot_omit_repeat_or_reorder_words(self):
        cases = [lambda d: d["sentences"].pop(),
                 lambda d: d["sentences"][0]["word_ids"].reverse(),
                 lambda d: d["sentences"].append({**d["sentences"][-1], "id": "s3"})]
        for index, mutate in enumerate(cases):
            with self.subTest(index=index):
                self.data = deepcopy(self.example)
                mutate(self.data)
                with self.assertRaisesRegex(ValueError, "每个词恰好引用一次"):
                    self.validate()

    def test_no_audio_has_empty_arrays_and_explicit_status(self):
        self.data["media"]["has_audio"] = False
        for name in ("speech", "silence", "words", "sentences"):
            self.data[name] = []
        for name in ("vad", "asr"):
            self.data["analyzers"][name]["status"] = "no_audio"
        self.validate()
        self.data["analyzers"]["vad"]["status"] = "ok"
        with self.assertRaisesRegex(ValueError, "no_audio"):
            self.validate()

    def test_no_audio_cannot_have_fake_silence(self):
        self.data["media"]["has_audio"] = False
        with self.assertRaisesRegex(ValueError, "必须为空"):
            self.validate()

    def test_silent_audio_is_distinct_from_missing_audio(self):
        for name in ("speech", "words", "sentences"):
            self.data[name] = []
        self.data["silence"] = [{"start": 0, "end": 10}]
        self.validate()

    def test_audio_status_cannot_claim_no_audio(self):
        self.data["analyzers"]["asr"]["status"] = "no_audio"
        with self.assertRaisesRegex(ValueError, "状态必须为 ok"):
            self.validate()

    def test_asr_and_vad_are_independent(self):
        self.data["speech"] = []
        self.data["silence"] = [{"start": 0, "end": 10}]
        self.validate()  # 两个检测器可以不一致，后续评测必须能看到这种情况。

    def test_cli_exit_status(self):
        command = [sys.executable, "-m", "clipforge.analysis.validation",
                   str(EXAMPLE_PATH), "--schema", str(SCHEMA_PATH)]
        valid = subprocess.run(command, capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr.decode(errors="replace"))
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "invalid.json"
            bad.write_text('{"schema_version": "bad"}', encoding="utf-8")
            command[3] = str(bad)
            invalid = subprocess.run(command, capture_output=True)
        self.assertEqual(invalid.returncode, 1)
        self.assertIn(b"INVALID:", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
