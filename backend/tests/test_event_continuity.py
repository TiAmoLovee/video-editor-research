"""Generic positive/negative cases for auditable multi-unit obligations."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from clipforge.decision import event_continuity as module
from clipforge.decision.event_continuity import build_event_continuity
from clipforge.decision.event_dependencies import build_event_dependencies


def units(*texts):
    return [{"start": index * 10, "end": index * 10 + 9,
             "sentences": [{"id": f"s{index}", "text": text}]}
            for index, text in enumerate(texts)]


class EventContinuityTests(unittest.TestCase):
    def report(self, *texts):
        return build_event_continuity(units(*texts))

    def new_links(self, *texts):
        source = units(*texts)
        old_count = len(build_event_dependencies(source)["links"])
        return build_event_continuity(source)["links"][old_count:]

    def test_base_rows_are_an_exact_unchanged_prefix(self):
        source = units("为什么会这样？", "他说‘还在讨论", "计划’。")
        before = deepcopy(source)
        old, new = build_event_dependencies(source), build_event_continuity(source)
        self.assertEqual(new["links"][:len(old["links"])], old["links"])
        self.assertEqual(new["open_edges"][:len(old["open_edges"])], old["open_edges"])
        self.assertEqual(source, before)
        self.assertEqual(new["base_version"], old["version"])
        self.assertEqual(new["version"], "surface-event-dependencies-v2")

    def test_implicit_interest_needs_an_explicit_substantive_reason(self):
        links = self.new_links("我会好奇这样的变化", "是因为成本提高了")
        self.assertEqual([(row["first_unit"], row["last_unit"], row["kind"])
                          for row in links], [(0, 1, "implicit_question_reason")])
        for response in ("所以我们换一个话题", "是因为", "嗯", "我觉得还可以"):
            with self.subTest(response=response):
                self.assertEqual(self.new_links("我想知道这样的变化", response), [])
                self.assertTrue(self.report("我想知道这样的变化", response)["ambiguities"])

    def test_no_intent_dependency_for_already_answered_negated_or_quoted_language(self):
        for text in ("我很好奇变化但后来已经查清楚了", "我会好奇变化因为成本提高了",
                     "我不再好奇这样的变化", "他说‘我会好奇这样的变化’",
                     "我想知道这个词", "‘好奇’这个词很常见"):
            with self.subTest(text=text):
                self.assertEqual(self.new_links(text, "是因为拍摄延迟了"), [])

    def test_causal_question_words_are_not_mistaken_for_same_unit_answers(self):
        for text in ("我想知道因为什么改变计划", "我好奇这个变化是因为什么",
                     "我想知道这次改变的原因是什么"):
            with self.subTest(text=text):
                self.assertTrue(any(row["kind"] == "implicit_question_reason"
                                    for row in self.new_links(text, "是因为预算不足")))

    def test_single_character_reasons_are_not_rejected_by_an_arbitrary_length(self):
        for question, reason in (("我想知道为什么延期", "因为雨"),
                                 ("我想知道这个变化的原因", "是因为钱")):
            with self.subTest(reason=reason):
                self.assertTrue(any(row["kind"] == "implicit_question_reason"
                                    for row in self.new_links(question, reason)))
        self.assertEqual(self.new_links("因为雨", "所以活动取消了")[0]["kind"], "cause_result")
        for reason in ("因为嗯", "是因为的", "是因为"):
            with self.subTest(reason=reason):
                self.assertEqual(self.new_links("我想知道为什么延期", reason), [])

    def test_pending_cause_requires_paired_result_and_not_result_alone(self):
        for cause, result in (("因为道路结冰", "所以活动取消了"),
                              ("由于预算不足", "因此计划推迟了"),
                              ("因为下雨", "所以今天的活动取消了"),
                              ("Because the road is icy", "Therefore the event is cancelled")):
            with self.subTest(cause=cause):
                self.assertEqual(self.new_links(cause, result)[0]["kind"], "cause_result")
        for cause, result in (("因为下雨所以取消了", "所以接下来谈器材"),
                              ("预算已经确定了", "所以接下来谈器材"),
                              ("因为下雨我们取消了活动。", "所以接下来谈器材"),
                              ("因为下雨活动已经取消", "所以接下来谈摄像器材"),
                              ("因为下雨", "所以我们接下来聊摄影"),
                              ("因为下雨", "所以我想换个话题"),
                              ("因为这个词很常见", "所以接下来谈器材")):
            with self.subTest(cause=cause):
                self.assertEqual(self.new_links(cause, result), [])

    def test_cause_is_a_complete_reason_answer_without_a_result_obligation(self):
        for question in ("为什么推迟？", "我想知道推迟的原因"):
            with self.subTest(question=question):
                report = self.report(question, "因为下雨", "今天介绍器材。")
                self.assertFalse(any(row["kind"] == "cause_result" for row in report["open_edges"]))
                self.assertFalse(any(row["kind"] == "cause_result" for row in report["links"]))
        same_unit = self.report("为什么延后？因为道路结冰", "今天介绍器材。")
        self.assertEqual(same_unit["links"], [])
        self.assertEqual(same_unit["open_edges"], [])
        self.assertEqual(same_unit["ambiguities"], [])

    def test_answer_does_not_close_after_one_unit_when_complements_continue(self):
        report = self.report("我会好奇这样的变化", "是因为成本提高了，我在生产方面",
                             "遇到了一些挑战，就是需要的一些材料",
                             "是我们之前很少使用的型号", "下面介绍另一个话题。")
        new = [row for row in report["links"] if row["kind"] != "question_response"]
        self.assertEqual([(row["first_unit"], row["last_unit"])
                          for row in new], [(0, 1), (1, 2), (2, 3)])
        self.assertEqual(report["obligations"][0]["first_unit"], 0)
        self.assertEqual(report["obligations"][0]["last_unit"], 3)
        self.assertEqual(report["obligations"][0]["closure"],
                         "no_further_surface_dependency_detected")
        self.assertFalse(report["semantic_completeness_verified"])

    def test_paired_definition_can_continue_beyond_an_aspect_response(self):
        links = self.new_links("我在研究上面", "得了一些挑战就是可能用的一些参数",
                               "是我之前不太常用的范围")
        self.assertEqual([(row["first_unit"], row["last_unit"], row["kind"])
                          for row in links], [(0, 1, "predicate_complement"),
                                             (1, 2, "definition_continuation")])

    def test_aspect_requires_a_dangling_tail_and_a_predicate(self):
        for left, right in (("我在研究方面取得了进展", "得了一些奖项"),
                            ("我的工作重点在创作上面", "遇到一些困难"),
                            ("资源都花在生产方面", "遇到一些困难"),
                            ("我在研究上面", "下面换一个话题"),
                            ("我在研究上面", "一个新故事开始了")):
            with self.subTest(left=left, right=right):
                self.assertEqual(self.new_links(left, right), [])

    def test_terminal_range_answer_is_not_an_unresolved_predicate_request(self):
        report = self.report("你在哪方面表现最好？", "我在研究方面。", "下面介绍其他事情。")
        self.assertEqual([(row["first_unit"], row["last_unit"], row["kind"])
                          for row in report["links"]], [(0, 1, "question_response")])
        self.assertEqual(report["open_edges"], [])
        self.assertTrue(report["ambiguities"])
        self.assertEqual(report["obligations"][0]["closure"],
                         "no_further_surface_dependency_detected")
        # Punctuation alone cannot discard an explicitly supplied continuation.
        self.assertEqual(self.new_links("我在生产方面。", "遇到了一些困难")[0]["kind"],
                         "predicate_complement")

    def test_copula_and_contrast_require_complements_not_new_topics(self):
        self.assertEqual(self.new_links("另外一个原因就是", "成本变高了")[0]["kind"],
                         "copular_continuation")
        self.assertEqual(self.new_links("但是这次的影响反而", "不是真正影响性能")[0]["kind"],
                         "contrast_continuation")
        for left, right in (("另外一个原因就是", "下面介绍器材"),
                            ("另外一个原因就是", "是的"),
                            ("对就是", "下面介绍器材"),
                            ("但是这次的影响反而", "另一个话题开始了")):
            with self.subTest(left=left, right=right):
                self.assertEqual(self.new_links(left, right), [])

    def test_pending_child_keeps_the_answer_chain_unresolved(self):
        report = self.report("我想知道这样的变化", "是因为成本增加，我在生产方面")
        self.assertEqual(report["obligations"][0]["last_unit"], 1)
        self.assertEqual(report["obligations"][0]["closure"], "unresolved_surface_dependency")
        self.assertTrue(any(row["unit"] == 1 and row["side"] == "right"
                            for row in report["open_edges"]))

    def test_source_edge_or_unmatched_strong_slot_is_unresolved(self):
        for text in ("我想知道这样的变化", "我在生产方面", "另一个原因就是"):
            with self.subTest(text=text):
                report = self.report(text)
                self.assertTrue(report["open_edges"])
                self.assertEqual(report["obligations"][0]["closure"], "unresolved_surface_dependency")

    def test_optional_nominal_definition_is_a_warning_without_paired_continuation(self):
        for response in ("是的", "下面谈别的话题", "这些材料很实用"):
            with self.subTest(response=response):
                report = self.report("选择的就是一些材料", response)
                self.assertEqual(report["links"], [])
                self.assertEqual(report["open_edges"], [])
                self.assertEqual(report["ambiguities"][0]["retention"],
                                 "warning_only_ambiguous_construction")

    def test_no_scan_past_substantive_intervening_unit(self):
        report = self.report("我会好奇这样的变化", "今天介绍器材。", "是因为成本提高了")
        self.assertEqual(report["links"], [])
        self.assertTrue(report["ambiguities"])

    def test_punctuation_separates_completed_intent_from_new_tail(self):
        self.assertEqual(self.new_links("我会好奇这个变化。后来我已经了解清楚了。", "是因为成本提高了"), [])

    def test_verbatim_source_character_spans_survive_multisentence_units(self):
        source = units("unused", "是因为成本提高了")
        source[0]["sentences"] = [{"id": "a", "text": "前面的事情已经讲完。"},
                                   {"id": "b", "text": "我会好奇这样的变化"}]
        proof = build_event_continuity(source)["links"][0]["evidence"]
        self.assertEqual(proof["left"]["source_sentences"], ["b"])
        self.assertEqual(proof["left"]["text"], "我会好奇这样的变化")
        self.assertEqual(proof["left"]["source_spans"],
                         [{"source_sentence": "b", "start_character": 0, "end_character": 9}])
        self.assertEqual(proof["right"]["text"], "是因为成本提高了")

    def test_quote_and_english_apostrophe_masking_do_not_rewrite_source(self):
        source = units("It's a writer's note. 我会好奇这样的变化", "是因为成本提高了")
        proof = build_event_continuity(source)["links"][0]["evidence"]
        self.assertEqual(proof["left"]["text"], "我会好奇这样的变化")
        self.assertEqual(self.new_links('他说"我在研究方面"', "遇到一些困难"), [])

    def test_quoted_prompts_remain_masked_across_unit_and_sentence_boundaries(self):
        source = units("他说：‘先介绍", "我会好奇这样的变化", "最后总结。’")
        source[1]["sentences"].append({"id": "within", "text": "我在研究方面"})
        old = build_event_dependencies(source)
        report = build_event_continuity(source)
        self.assertEqual(report["links"], old["links"])
        self.assertEqual(report["open_edges"], old["open_edges"])
        self.assertEqual(report["ambiguities"], [])

    def test_active_answer_retains_enumerated_and_causal_contrast_continuations(self):
        report = self.report("我会好奇这样的变化", "是因为有一部分设备旧了",
                             "另外一个是可能成本提高就是", "材料会更昂贵但是设备反而",
                             "不是真的损坏了，其实设备应该没什么问题",
                             "但是因为现在不能完整利用，所以我在生产方面",
                             "遇到了一些挑战就是可能用的一些材料",
                             "是我们之前很少使用的型号")
        self.assertEqual([(row["first_unit"], row["last_unit"])
                          for row in report["links"]], [(i, i + 1) for i in range(7)])
        self.assertEqual(report["obligations"][0]["first_unit"], 0)
        self.assertEqual(report["obligations"][0]["last_unit"], 7)
        for index in (1, 4):
            self.assertEqual(report["links"][index]["evidence"]["context_link_indices"], [index - 1])

    def test_enumeration_alone_does_not_invent_an_answer_context(self):
        for left, right in (("旧设备还能使用", "另外一个是可能成本提高就是"),
                            ("旧设备还能使用", "但是因为现在不能完整利用"),
                            ("是因为旧设备损坏", "另外一个是预算提高了")):
            with self.subTest(left=left):
                self.assertEqual(self.new_links(left, right), [])

    def test_explicit_topic_switch_inside_answer_unit_resets_incoming_context(self):
        report = self.report("我想知道为什么延期", "是因为天气。下面换个话题。",
                             "另一个原因是价格低。")
        self.assertEqual([(row["first_unit"], row["last_unit"], row["kind"])
                          for row in report["links"]], [(0, 1, "implicit_question_reason")])
        self.assertEqual(report["obligations"][0]["last_unit"], 1)
        self.assertFalse(any(row["kind"] == "reason_enumeration" for row in report["links"]))

    def test_ordinary_cause_without_a_paired_result_is_only_ambiguous_inside_source(self):
        report = self.report("因为供应变化让我们暂时推迟计划", "后来我们再商量。")
        self.assertEqual(report["links"], [])
        self.assertEqual(report["open_edges"], [])
        self.assertEqual(report["ambiguities"][0]["kind"], "cause_result")

    def test_source_final_cause_can_already_include_its_implicit_result(self):
        for text in ("因为下雨活动已经取消", "因为下雨我们取消活动。", "因为道路结冰"):
            with self.subTest(text=text):
                report = self.report(text)
                self.assertEqual(report["links"], [])
                self.assertEqual(report["open_edges"], [])

    def test_raw_time_gaps_do_not_override_or_invent_textual_policy(self):
        source = units("我在研究方面", "遇到了一些困难")
        source[1].update(start=1000, end=1001)
        report = build_event_continuity(source)
        self.assertEqual(len(report["links"]), 1)
        self.assertNotIn("retention", report["links"][0])
        self.assertFalse(report["semantic_completeness_verified"])

    def test_empty_input_is_deterministic_and_no_model_call_is_claimed(self):
        report = build_event_continuity([])
        self.assertEqual(report["links"], [])
        self.assertEqual(report["obligations"], [])
        self.assertEqual(report["model_requests"], 0)
        self.assertEqual(json.dumps(report, ensure_ascii=False),
                         json.dumps(build_event_continuity([]), ensure_ascii=False))

    def test_limits_fail_explicitly_without_partial_evidence(self):
        source = units("我在生产方面", "遇到一些困难就是", "成本增加了")
        with patch.object(module, "MAX_DEPENDENCIES", 1):
            with self.assertRaisesRegex(ValueError, "evidence limit exceeded"):
                build_event_continuity(source)

    def test_many_links_keep_linear_indexed_chain_evidence(self):
        source = units(*(["我在生产方面"] * 30 + ["遇到一些困难"]))
        # Each unmatched slot records one short proof, never a repeated chain.
        report = build_event_continuity(source)
        self.assertLessEqual(len(report["links"]) + len(report["open_edges"]), len(source))
        for row in report["links"]:
            self.assertLessEqual(len(row["evidence"]["left"]["text"]), module.MAX_MATCH_CHARACTERS)

    def test_bounded_suffix_is_not_treated_as_an_actual_clause_start(self):
        suffix = "因为" + "雨" * (module.MAX_MATCH_CHARACTERS - 2)
        report = self.report("我们不会" + suffix, "所以活动取消了")
        self.assertEqual(report["links"], [])
        self.assertEqual(report["open_edges"], [])
        self.assertEqual(report["ambiguities"][0]["kind"], "long_clause_not_parsed")
        self.assertLessEqual(len(report["ambiguities"][0]["evidence"]["left"]["text"]),
                             module.MAX_MATCH_CHARACTERS)

    def test_input_validation_is_inherited_from_v1(self):
        for bad in (None, [None], [{"start": 0, "end": 1, "sentences": []}]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    build_event_continuity(bad)


if __name__ == "__main__":
    unittest.main()
