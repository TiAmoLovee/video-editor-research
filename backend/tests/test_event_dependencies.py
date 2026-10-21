"""General discourse constructions, counterexamples and bounded evidence."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from clipforge.decision import event_dependencies as module
from clipforge.decision.event_dependencies import build_event_dependencies


def units(*texts):
    return [{"start": index * 10, "end": index * 10 + 9,
             "sentences": [{"id": f"s{index}", "text": text}]}
            for index, text in enumerate(texts)]


class EventDependencyTests(unittest.TestCase):
    def links(self, *texts):
        return build_event_dependencies(units(*texts))["links"]

    def test_explicit_question_and_response_are_adjacent_not_a_whole_interview(self):
        links = self.links("为什么改变计划？", "预算缩减了。", "下面谈天气。")
        self.assertEqual([(row["first_unit"], row["last_unit"], row["kind"])
                          for row in links], [(0, 1, "question_response")])
        self.assertEqual(links[0]["evidence"]["text"], "为什么改变计划？")

    def test_english_question_and_quoted_terminal_question(self):
        for text in ("Why did you change?", '“为什么改变？”', "「为什么改变？」"):
            with self.subTest(text=text):
                self.assertEqual(self.links(text, "The budget changed.")[0]["kind"],
                                 "question_response")

    def test_unpunctuated_question_forms_without_arbitrary_internal_question_words(self):
        for text in ("为什么改变计划", "你觉得背后是什么样的原因", "你怎么看",
                     "How did the plan change"):
            with self.subTest(text=text):
                self.assertEqual(self.links(text, "成本提高了")[0]["kind"], "question_response")
        for text in ("我不知道为什么", "他解释了为什么改变计划。", "什么都准备好了",
                     "我们讨论如何调整。", "我已经知道该怎么做", "那个很好。"):
            with self.subTest(text=text):
                self.assertEqual(self.links(text, "一个新话题。"), [])

    def test_question_and_answer_already_in_same_unit_do_not_pull_next_topic(self):
        for text in ("为什么改？因为预算不足。", "为什么改因为预算不足",
                     "Why did it change? The cost increased."):
            with self.subTest(text=text):
                self.assertEqual(self.links(text, "下面谈天气。"), [])
        grouped = units("预算不足。", "下面谈天气。")
        grouped[0]["sentences"].insert(0, {"id": "q", "text": "为什么改？"})
        self.assertEqual(build_event_dependencies(grouped)["links"], [])

    def test_colon_introduction_but_completed_list_is_closed(self):
        self.assertEqual(self.links("有两个步骤：", "先关机再断电。")[0]["kind"],
                         "introduction_continuation")
        self.assertEqual(self.links("步骤如下：关机，断电。", "另一个话题。"), [])

    def test_demonstrative_modifier_needs_unfinished_tail_and_a_nominal_head(self):
        for tail, head in (("我们正处在那个", "阶段需要更谨慎"),
                           ("我没有预料到这个", "结果会发生"),
                           ("我们后来才看懂那个", "时候的变化")):
            with self.subTest(tail=tail, head=head):
                links = self.links(tail, head)
                self.assertEqual(links[0]["kind"], "modifier_head_continuation")
                self.assertEqual(links[0]["evidence"]["next_text"], head)
        for tail, head in (("那个很好。", "时候已经很晚"),
                           ("我就选这个", "另一个话题开始了"),
                           ("我选择那个。", "原因很简单"),
                           ("他说‘这个’", "结果很意外")):
            with self.subTest(tail=tail, head=head):
                self.assertEqual(self.links(tail, head), [])

    def test_unresolved_source_ends_are_evidence_not_semantic_certification(self):
        for text, kind in (("怎么办？", "question_response"),
                           ("请听下面这段：", "introduction_continuation"),
                           ("我后来选择了那个", "unresolved_demonstrative")):
            with self.subTest(text=text):
                result = build_event_dependencies(units(text))
                self.assertEqual(result["links"], [])
                self.assertEqual(result["open_edges"][0]["kind"], kind)
                self.assertEqual(result["open_edges"][0]["side"], "right")
                self.assertFalse(result["semantic_completeness_verified"])

    def test_nested_and_repeated_quotes_have_exact_source_points(self):
        result = build_event_dependencies(units("他说：“我听到‘你好", "朋友’然后离开。”", "‘再见’。"))
        spans = [row for row in result["links"] if row["kind"] == "paired_quotation"]
        self.assertEqual(len(spans), 2)
        self.assertEqual([(row["first_unit"], row["last_unit"]) for row in spans], [(0, 1), (0, 1)])
        self.assertEqual(spans[0]["evidence"]["opening"]["source_sentence"], "s0")
        self.assertEqual(result["open_edges"], [])
        self.assertEqual(self.links('他说"您好"然后"再见"。', "其他话题。"), [])

    def test_unmatched_quote_ends_and_mismatched_nesting_are_reported(self):
        result = build_event_dependencies(units("朋友’说了什么。", "他说：“你好。"))
        self.assertEqual([(row["unit"], row["side"], row["kind"]) for row in result["open_edges"]],
                         [(0, "left", "quotation_missing_opener"),
                          (1, "right", "quotation_missing_closer")])
        result = build_event_dependencies(units("“‘开头", "”"))
        self.assertEqual(len(result["open_edges"]), 3)

    def test_apostrophes_inside_english_words_are_not_quotes(self):
        for text in ("It's a writer's work.", "It’s a writer’s work.", "The writers' room is quiet."):
            with self.subTest(text=text):
                result = build_event_dependencies(units(text))
                self.assertEqual(result["links"], [])
                self.assertEqual(result["open_edges"], [])
        result = build_event_dependencies(units("'A long", "quotation'."))
        self.assertEqual(result["links"][0]["kind"], "paired_quotation")
        result = build_event_dependencies(units("He said 'hello' and left.", "Other news."))
        self.assertEqual(result["links"], [])
        self.assertEqual(result["open_edges"], [])
        result = build_event_dependencies(units("He said 'a writer's", "work' and left."))
        self.assertEqual(result["links"][0]["kind"], "paired_quotation")
        self.assertEqual(result["open_edges"], [])

    def test_hard_media_pause_is_left_to_caller_not_silently_deleted(self):
        data = units("为什么？", "因为费用增加。")
        data[1].update(start=100, end=110)
        self.assertEqual(build_event_dependencies(data)["links"][0]["last_unit"], 1)

    def test_input_is_immutable_and_output_is_deterministic_json(self):
        data = units("为什么改变", "因为费用。", "他说：“你好", "再见。”")
        before = deepcopy(data)
        result = build_event_dependencies(data)
        self.assertEqual(result, build_event_dependencies(data))
        self.assertEqual(data, before)
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)
        self.assertEqual(result["model_requests"], 0)
        self.assertEqual(build_event_dependencies([])["links"], [])

    def test_resource_limits_fail_explicitly_without_partial_results_or_input_edits(self):
        data = units("为什么？", "为什么？", "因为费用。")
        before = deepcopy(data)
        for constant, ceiling, pattern in (("MAX_UNITS", 2, "unit limit"),
                                           ("MAX_SENTENCES", 2, "sentence limit"),
                                           ("MAX_TEXT_CHARACTERS", 2, "text limit"),
                                           ("MAX_DEPENDENCIES", 1, "evidence limit")):
            with self.subTest(constant=constant), patch.object(module, constant, ceiling):
                with self.assertRaisesRegex(ValueError, pattern):
                    build_event_dependencies(data)
        with patch.object(module, "MAX_QUOTE_DEPTH", 2):
            with self.assertRaisesRegex(ValueError, "depth limit"):
                build_event_dependencies(units("“‘「太深」’”"))
        self.assertEqual(data, before)

    def test_quotation_evidence_is_linear_without_repeating_full_span_text(self):
        sizes = []
        for count in (100, 200):
            data = units('“开始', *["中间内容。"] * count, '结束。”')
            result = build_event_dependencies(data)
            self.assertEqual(len(result["links"]), 1)
            self.assertEqual(result["links"][0]["last_unit"], count + 1)
            sizes.append(len(json.dumps(result, ensure_ascii=False)))
        self.assertLess(sizes[1], 2 * sizes[0])

    def test_malformed_units_reject_finite_time_and_source_contract_violations(self):
        cases = [None, {}, [{"start": 0, "end": 1, "sentences": []}],
                 [{"start": True, "end": 1, "sentences": [{"id": "x", "text": "a"}]}]]
        for number in (float("nan"), float("inf"), -1):
            data = units("内容")
            data[0]["start"] = number
            cases.append(data)
        data = units("甲", "乙")
        data[1]["start"] = 1
        cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                build_event_dependencies(data)


if __name__ == "__main__":
    unittest.main()
