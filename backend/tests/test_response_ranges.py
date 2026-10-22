"""Whole-response scope regressions; no calls and no semantic quality labels."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clipforge.decision import response_ranges as module
from clipforge.editing.word_spans import content_hash
from test_model_event_plan import fixture
from test_relation_event_plan import analysis_with_words, proposal


def blocks_fixture(texts):
    analysis = analysis_with_words(len(texts))
    for index, text in enumerate(texts):
        analysis["words"][index]["text"] = text
        analysis["sentences"][index]["text"] = text
    return analysis


def regroup(analysis, ends):
    first, groups = 1, []
    for index, end in enumerate(ends):
        words = analysis["words"][first - 1:end]
        groups.append({"id": f"s{index}", "start": words[0]["start"], "end": words[-1]["end"],
                       "text": "".join(w["text"] for w in words), "word_ids": [w["id"] for w in words]})
        first = end + 1
    analysis["sentences"] = groups
    return analysis


class ResponseRangeTests(unittest.TestCase):
    def test_v2_and_v3_short_answer_seeds_produce_same_full_available_reply(self):
        analysis = regroup(analysis_with_words(484),
            [31, 48, 72, 104, 131, 168, 184, 208, 215, 232, 278, 310, 321, 346,
             361, 366, 374, 394, 414, 431, 441, 459, 484])
        for seed in (["q", 347, 361, 362, 366], ["q", 352, 361, 367, 374]):
            with self.subTest(seed=seed):
                source = proposal(analysis, cuts=[361, 374, 405, 431, 452], relations=[seed])
                before = deepcopy(source)
                report = module.recognize_response_ranges(analysis, source)
                scope = report["response_ranges"][0]
                self.assertEqual(scope["antecedent_span"]["first_word_index"], 347)
                self.assertEqual(scope["response_span"]["first_word_index"], 362)
                self.assertEqual(scope["response_span"]["last_word_index"], 484)
                self.assertEqual(scope["endpoint"]["reason"], "unresolved_source_end")
                self.assertEqual(report["generated_plan"]["selected_cuts_after_words"], [346])
                self.assertEqual(source, before)

    def test_whole_answer_keeps_elaboration_and_causal_result_until_next_question(self):
        analysis = blocks_fixture(["你为什么选这个方案？", "因为成本低。", "后来也遇到问题。",
                                   "所以我又作了改进。", "下一种方案呢？", "这是另一个回答。"])
        source = proposal(analysis, cuts=[1, 2, 3, 4, 5],
                          relations=[["q", 1, 1, 2, 2], ["c", 2, 2, 3, 3], ["q", 5, 5, 6, 6]])
        report = module.recognize_response_ranges(analysis, source)
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 4)
        self.assertEqual(report["response_ranges"][1]["response_span"]["last_word_index"], 4)
        self.assertEqual(report["generated_plan"]["selected_cuts_after_words"], [4])
        self.assertFalse(report["response_ranges"][0]["endpoint"]["semantic_closure_verified"])

    def test_explicit_topic_transition_ends_scope_before_unrelated_content(self):
        analysis = blocks_fixture(["请问为什么这样做？", "因为一开始有困难", "后来我改变了方法",
                                   "所以现在已经解决", "好的，那我们下面讨论预算", "这是另外一件事"])
        source = proposal(analysis, relations=[["q", 1, 1, 2, 2], ["c", 2, 2, 3, 3]])
        report = module.recognize_response_ranges(analysis, source)
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 4)
        self.assertNotIn("预算", report["response_ranges"][0]["response_span"]["text"])
        self.assertEqual(report["generated_plan"]["selected_cuts_after_words"], [4])

    def test_acknowledgment_without_topic_switch_does_not_close_a_reply(self):
        analysis = blocks_fixture(["你怎么看？", "我先解释", "好的", "嗯，我再补充一点", "补充内容"])
        report = module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 1, 2, 2]]))
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 5)

    def test_unverified_rhetorical_question_does_not_invent_a_speaker_change(self):
        analysis = blocks_fixture(["你怎么看这件事？", "我先解释原因", "为什么会这样？", "因为条件发生变化",
                                   "你下一步计划是什么？", "我要继续做实验"])
        source = proposal(analysis, relations=[["q", 1, 1, 2, 2], ["q", 5, 5, 6, 6]])
        report = module.recognize_response_ranges(analysis, source)
        scope = report["response_ranges"][0]
        self.assertEqual(scope["response_span"]["last_word_index"], 4)
        self.assertIn(3, [r["first_word"] for r in scope["possible_unverified_questions_inside_response"]])

    def test_quoted_question_seed_does_not_end_the_outer_reply(self):
        analysis = blocks_fixture(["你怎么看？", "我举一个例子", "“为什么会这样？”", "这是引用里的回答",
                                   "你下一步准备什么？", "准备下次实验"])
        source = proposal(analysis, relations=[["q", 1, 1, 2, 2], ["q", 3, 3, 4, 4], ["q", 5, 5, 6, 6]])
        report = module.recognize_response_ranges(analysis, source)
        self.assertEqual(report["quoted_question_seed_indices"], [1])
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 4)
        self.assertEqual(report["generated_plan"]["selected_cuts_after_words"], [4])

    def test_lead_in_blocks_between_question_and_answer_seed_are_preserved(self):
        analysis = regroup(analysis_with_words(10), [2, 4, 6, 10])
        report = module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 2, 5, 6]]))
        self.assertEqual(report["response_ranges"][0]["response_span"]["first_word_index"], 3)
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 10)

    def test_mixed_question_and_answer_inside_one_block_does_not_drop_answer_prefix(self):
        analysis = regroup(analysis_with_words(8), [4, 8])
        report = module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 2, 3, 3]]))
        scope = report["response_ranges"][0]
        self.assertTrue(scope["mixed_question_response_block"])
        self.assertEqual(scope["response_span"]["first_word_index"], 3)
        self.assertEqual(scope["response_span"]["last_word_index"], 8)

    def test_source_ending_at_an_unfinished_lead_in_remains_unresolved(self):
        analysis = blocks_fixture(["你怎么想？", "我就觉得那"])
        report = module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 1, 2, 2]]))
        scope = report["response_ranges"][0]
        self.assertEqual(scope["response_span"]["text"], "我就觉得那")
        self.assertEqual(scope["endpoint"]["reason"], "unresolved_source_end")
        self.assertTrue(scope["endpoint"]["source_end_is_not_semantic_closure"])
        self.assertFalse(scope["semantic_completeness_verified"])

    def test_causal_elaboration_is_kept_without_requiring_a_question_seed(self):
        analysis = blocks_fixture(["因为原来的条件不够", "所以我先作了改进", "后来又作了一次验证",
                                   "下面我们讨论另一件事", "后续安排"])
        report = module.recognize_response_ranges(analysis, proposal(analysis, relations=[["c", 1, 1, 2, 2]]))
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 3)
        self.assertEqual(report["generated_plan"]["selected_cuts_after_words"], [3])

    def test_required_boundary_is_an_interruption_not_semantic_closure(self):
        analysis = analysis_with_words(6)
        source = proposal(analysis, relations=[["q", 1, 1, 2, 2]], required=[3])
        report = module.recognize_response_ranges(analysis, source)
        self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], 3)
        self.assertEqual(report["response_ranges"][0]["endpoint"]["reason"], "interrupted_by_required_boundary")

    def test_required_boundary_inside_block_or_seed_is_rejected(self):
        analysis = regroup(analysis_with_words(6), [3, 6])
        with self.assertRaisesRegex(ValueError, "inside_response_ownership_block"):
            module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 1, 2, 2]], required=[2]))
        analysis = analysis_with_words(6)
        with self.assertRaisesRegex(ValueError, "crosses_new_question"):
            module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 1, 2, 5]], required=[3]))

    def test_new_question_inside_a_declared_answer_is_a_conflict_not_silent_truncation(self):
        analysis = analysis_with_words(8)
        source = proposal(analysis, relations=[["q", 1, 1, 2, 6], ["q", 5, 5, 7, 8]])
        with self.assertRaisesRegex(ValueError, "crosses_new_question"):
            module.recognize_response_ranges(analysis, source)

    def test_music_annotation_source_words_and_clocks_are_unchanged(self):
        analysis, _, evidence = fixture()
        source = proposal(analysis, relations=[["q", 2, 2, 3, 4]], evidence=[evidence])
        before = deepcopy((analysis, source))
        report = module.recognize_response_ranges(analysis, source)
        self.assertEqual((analysis, source), before)
        self.assertEqual(sum(len(e["retained_non_dialogue_examples"]) for e in report["generated_plan"]["events"]), 1)
        self.assertEqual([w for e in report["generated_plan"]["events"] for w in e["word_span"]["source_words"]],
                         [w["id"] for w in analysis["words"]])

    def test_overlong_response_remains_whole_and_not_automatically_accepted(self):
        analysis = analysis_with_words(6)
        for name in ("words", "sentences", "shots", "speech"):
            for row in analysis[name]:
                row["start"] *= 40
                row["end"] *= 40
        analysis["media"]["duration_seconds"] *= 40
        report = module.recognize_response_ranges(analysis, proposal(analysis, relations=[["q", 1, 1, 2, 2]]))
        plan = report["generated_plan"]
        self.assertEqual(len(plan["events"]), 1)
        self.assertFalse(plan["events"][0]["event_extent"]["within_current_15_90_second_limit"])
        self.assertFalse(report["automatically_recommendable"])

    def test_rehashed_report_cannot_shorten_the_response_and_pass_reconstruction(self):
        analysis = analysis_with_words(8)
        source = proposal(analysis, relations=[["q", 1, 1, 2, 2]])
        report = module.recognize_response_ranges(analysis, source)
        self.assertTrue(module.verify_response_ranges(report, analysis, source))
        report["response_ranges"][0]["response_span"]["last_word_index"] = 2
        report.pop("report_sha256")
        report["report_sha256"] = content_hash(report)
        with self.assertRaisesRegex(ValueError, "reconstruction"):
            module.verify_response_ranges(report, analysis, source)

    def test_random_short_seeds_cannot_end_the_response_before_the_next_question(self):
        rng = random.Random(20261021)
        for _ in range(60):
            count = rng.randrange(6, 18)
            second = rng.randrange(4, count)
            analysis = analysis_with_words(count)
            d = rng.randrange(2, second)
            source = proposal(analysis, cuts=list(range(1, count)),
                relations=[["q", 1, 1, 2, d], ["q", second, second, second + 1, second + 1]])
            report = module.recognize_response_ranges(analysis, source)
            self.assertEqual(report["response_ranges"][0]["response_span"]["last_word_index"], second - 1)
            self.assertEqual(report["response_ranges"][1]["response_span"]["last_word_index"], count)
            self.assertEqual(report["generated_plan"]["selected_cuts_after_words"], [second - 1])


class ResponseRangeCLITests(unittest.TestCase):
    def test_offline_entry_keeps_inputs_and_refuses_to_overwrite_or_create_invalid_output(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            analysis = analysis_with_words(6)
            source = proposal(analysis, relations=[["q", 1, 1, 2, 2]])
            a, p = root / "analysis.json", root / "proposal.json"
            a.write_text(json.dumps(analysis), encoding="utf-8")
            p.write_text(json.dumps(source), encoding="utf-8")
            before = p.read_bytes()
            args = ["--analysis", str(a), "--proposal", str(p), "--out", str(root / "draft")]
            with redirect_stdout(io.StringIO()), patch("clipforge.decision.llm.http_transport",
                    side_effect=AssertionError("offline response recognizer contacted provider")):
                self.assertEqual(module.main(args), 0)
                self.assertEqual(module.main(args), 1)
                self.assertEqual(p.read_bytes(), before)
                source["required_boundaries"] = [1]
                source["relations"] = [["q", 1, 1, 2, 3]]
                p.write_text(json.dumps(source), encoding="utf-8")
                self.assertEqual(module.main(args[:-1] + [str(root / "rejected")]), 1)
                self.assertFalse((root / "rejected").exists())


if __name__ == "__main__":
    unittest.main()
