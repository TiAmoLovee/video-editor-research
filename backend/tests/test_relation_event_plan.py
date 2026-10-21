"""Offline relation-ownership invariants, including the rejected v2 cut pattern."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clipforge.decision import relation_event_plan as module
from clipforge.editing.word_spans import content_hash
from test_model_event_plan import fixture


def analysis_with_words(count):
    analysis, _, _ = fixture()
    analysis["words"] = [{"id": f"w{i}", "text": f"词{i}", "start": i + .1, "end": i + .8,
                           "probability": .9} for i in range(count)]
    analysis["sentences"] = [{"id": f"s{i}", "text": word["text"], "start": word["start"],
                               "end": word["end"], "word_ids": [word["id"]]}
                              for i, word in enumerate(analysis["words"])]
    analysis["media"]["duration_seconds"] = count + 1
    for name in ("shots", "speech"):
        analysis[name] = [{"start": 0, "end": count + 1}]
    return analysis


def proposal(analysis, cuts=(), relations=(), required=(), uncertain=(), evidence=()):
    return {"schema_version": "0.1.0-research.1", "artifact_kind": "relation_boundary_proposal",
            "analysis_sha256": content_hash(analysis),
            "normalized_sha256": analysis["media"]["normalized_sha256"],
            "origin": {"kind": "synthetic_test", "reference_sha256": "d" * 64},
            "boundary_hints": list(cuts), "relations": deepcopy(list(relations)),
            "required_boundaries": list(required), "uncertain_word_ranges": deepcopy(list(uncertain)),
            "non_dialogue_evidence": deepcopy(list(evidence))}


class RelationFirstTests(unittest.TestCase):
    def setUp(self):
        self.analysis = analysis_with_words(10)

    def build(self, **kwargs):
        source = proposal(self.analysis, **kwargs)
        return module.build_relation_event_plan(self.analysis, source)

    def test_rejected_v2_numbers_have_four_conflicts_removed_before_partitioning(self):
        analysis = analysis_with_words(484)
        ends = [31, 48, 72, 104, 131, 168, 184, 208, 232, 278, 310, 361, 405, 431, 452, 484]
        relations = [["c", 73, 104, 105, 131], ["q", 347, 361, 362, 366],
                     ["i", 367, 374, 375, 394], ["c", 395, 405, 415, 431],
                     ["c", 432, 452, 453, 484]]
        source = proposal(analysis, cuts=ends[:-1], relations=relations, uncertain=[[1, 31], [453, 484]])
        before = deepcopy(source)
        plan = module.build_relation_event_plan(analysis, source)
        self.assertEqual([r["after_word"] for r in plan["boundary_audit"] if not r["selected"]],
                         [104, 361, 405, 452])
        self.assertEqual(len(plan["events"]), 12)
        self.assertEqual(len(plan["relation_ownership"]), 5)
        self.assertEqual(source, before)
        self.assertFalse(plan["semantic_completeness_verified"])
        self.assertEqual(plan["producer"]["kind"], "deterministic_relation_first_generator")

    def test_transitive_QA_cause_chain_is_indivisible(self):
        plan = self.build(cuts=list(range(1, 10)), relations=[
            ["q", 2, 2, 3, 4], ["c", 4, 4, 5, 7], ["i", 6, 6, 8, 9]])
        self.assertEqual(plan["selected_cuts_after_words"], [1, 9])
        self.assertEqual(plan["protected_relation_groups"], [
            {"first_word": 2, "last_word": 9, "relation_indices": [0, 1, 2]}])
        self.assertEqual(plan["events"][1]["relation_indices"], [0, 1, 2])

    def test_adjacent_independent_relations_keep_the_boundary_between_them(self):
        plan = self.build(cuts=[1, 3, 6, 9], relations=[["q", 2, 2, 3, 3], ["c", 4, 4, 6, 6]])
        self.assertEqual(plan["selected_cuts_after_words"], [1, 3, 6, 9])
        self.assertEqual(len(plan["protected_relation_groups"]), 2)

    def test_nested_and_entire_question_span_cuts_are_blocked(self):
        plan = self.build(cuts=[1, 2, 3, 4, 5, 8, 9], relations=[
            ["q", 2, 4, 5, 9], ["c", 3, 3, 7, 8]])
        self.assertEqual(plan["selected_cuts_after_words"], [1, 9])
        self.assertEqual(plan["protected_relation_groups"][0]["relation_indices"], [0, 1])

    def test_required_media_or_topic_boundary_conflict_stops_generation(self):
        with self.assertRaisesRegex(ValueError, "required_boundary"):
            self.build(cuts=[1, 4, 8], relations=[["q", 2, 3, 5, 8]], required=[4])

    def test_required_boundaries_outside_relations_are_retained_even_without_hint(self):
        plan = self.build(relations=[["q", 2, 3, 5, 8]], required=[1, 8])
        self.assertEqual(plan["selected_cuts_after_words"], [1, 8])

    def test_event_limit_does_not_delete_extra_safe_hints_to_fit(self):
        analysis = analysis_with_words(14)
        with self.assertRaisesRegex(ValueError, "too_many_generated_events"):
            module.build_relation_event_plan(analysis, proposal(analysis, cuts=list(range(1, 14))))

    def test_duration_cap_never_splits_a_linked_event(self):
        for name in ("words", "sentences", "shots", "speech"):
            for row in self.analysis[name]:
                row["start"] *= 40
                row["end"] *= 40
        self.analysis["media"]["duration_seconds"] *= 40
        plan = self.build(cuts=list(range(1, 10)), relations=[["c", 1, 4, 5, 10]])
        self.assertEqual(len(plan["events"]), 1)
        extent = plan["events"][0]["event_extent"]
        self.assertGreater(extent["duration_seconds"], 90)
        self.assertFalse(extent["within_current_15_90_second_limit"])

    def test_absent_links_and_valid_partition_never_grant_quality_approval(self):
        plan = self.build(cuts=[2, 5, 8])
        self.assertFalse(plan["review_readiness"]["relation_inventory_complete"])
        self.assertTrue(plan["review_readiness"]["unreported_relationships_may_still_be_split"])
        self.assertEqual(plan["review_readiness"]["automatic_candidate_count"], 0)
        for row in [plan] + plan["events"]:
            self.assertFalse(row["production_candidate"])
            self.assertFalse(row["automatically_recommendable"])
            self.assertFalse(row["semantic_completeness_verified"])

    def test_uncertainty_is_attached_to_word_ranges_and_survives_changed_event_count(self):
        plan = self.build(cuts=[1, 3, 7, 9], relations=[["q", 2, 2, 5, 7]], uncertain=[[3, 4]])
        self.assertEqual(len(plan["events"]), 4)
        self.assertTrue(plan["events"][0]["uncertainty"]["source_edge"])
        self.assertEqual(plan["events"][1]["uncertainty"]["intersecting_word_ranges"], [[3, 4]])
        self.assertTrue(plan["events"][-1]["uncertainty"]["declared_uncertain"])

    def test_music_example_is_retained_without_changing_words_times_or_annotation(self):
        analysis, _, evidence = fixture()
        source = proposal(analysis, cuts=[1, 4], relations=[["i", 2, 2, 3, 4]], evidence=[evidence])
        before = deepcopy((analysis, source))
        plan = module.build_relation_event_plan(analysis, source)
        event = plan["events"][1]
        self.assertEqual(event["word_span"]["end_seconds"], 3.8)
        self.assertEqual(event["event_extent"]["end_seconds"], 7.1)
        self.assertEqual(event["retained_non_dialogue_examples"][0]["after_word"], 4)
        self.assertEqual((analysis, source), before)
        self.assertFalse(plan["edl_compatible"])

    def test_invalid_duplicate_backwards_overlapping_or_out_of_range_relations_rejected(self):
        for links in ([["q", 2, 3, 3, 4]], [["q", 4, 5, 2, 3]], [["q", 2, 3, 4, 11]],
                      [["q", True, 3, 4, 5]], [["q", 2., 3, 4, 5]], [["bad", 2, 3, 4, 5]],
                      [["q", 2, 3, 4, 5]] * 2, [["q", 2, 3, 4]]):
            with self.subTest(links=links), self.assertRaises(ValueError):
                self.build(relations=links)

    def test_invalid_boundary_lists_are_rejected_without_reordering_or_rounding(self):
        for cuts in ([0], [10], [2, 2], [4, 2], [True], [1.5]):
            with self.subTest(cuts=cuts), self.assertRaises(ValueError):
                self.build(cuts=cuts)

    def test_wrong_source_unknown_fields_and_origin_rejected(self):
        for name, value in (("analysis_sha256", "a" * 64), ("normalized_sha256", "c" * 64),
                            ("origin", {"kind": "approved", "reference_sha256": "d" * 64}),
                            ("approved", True)):
            source = proposal(self.analysis)
            source[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.build_relation_event_plan(self.analysis, source)

    def test_rehashed_plan_cannot_remove_relations_or_change_safe_cuts(self):
        source = proposal(self.analysis, cuts=[1, 3, 8], relations=[["q", 2, 3, 5, 8]])
        plan = module.build_relation_event_plan(self.analysis, source)
        self.assertTrue(module.verify_relation_event_plan(plan, self.analysis, source))
        plan["selected_cuts_after_words"] = [1, 3, 8]
        plan.pop("plan_sha256")
        plan["plan_sha256"] = content_hash(plan)
        with self.assertRaisesRegex(ValueError, "reconstruction"):
            module.verify_relation_event_plan(plan, self.analysis, source)

    def test_resource_and_recursive_data_fail_without_truncation(self):
        with patch.object(module, "MAX_RELATIONS", 0), self.assertRaises(ValueError):
            self.build(relations=[["q", 2, 3, 4, 5]])
        source = proposal(self.analysis)
        source["relations"].append(source)
        with self.assertRaises(ValueError):
            module.build_relation_event_plan(self.analysis, source)

    def test_random_relation_sets_match_independent_direct_interval_oracle(self):
        rng = random.Random(20261021)
        for _ in range(100):
            count = rng.randrange(4, 12)
            analysis = analysis_with_words(count)
            links = []
            for _ in range(rng.randrange(1, 8)):
                a, b, c, d = sorted(rng.sample(range(1, count + 1), 4))
                row = [rng.choice(["q", "c", "i", "x"]), a, b, c, d]
                if row not in links:
                    links.append(row)
            hints = sorted(rng.sample(range(1, count), rng.randrange(count)))
            plan = module.build_relation_event_plan(analysis, proposal(analysis, cuts=hints, relations=links))
            expected = [k for k in hints if not any(a <= k < d for _, a, b, c, d in links)]
            self.assertEqual(plan["selected_cuts_after_words"], expected)
            self.assertEqual([w for e in plan["events"] for w in e["word_span"]["source_words"]],
                             [w["id"] for w in analysis["words"]])
            for _, a, b, c, d in links:
                self.assertEqual(sum(e["word_span"]["first_word_index"] <= a <= d <=
                                     e["word_span"]["last_word_index"] for e in plan["events"]), 1)

    def test_relation_order_does_not_change_generated_word_ranges(self):
        links = [["q", 2, 2, 3, 5], ["c", 4, 4, 6, 8]]
        a = self.build(cuts=list(range(1, 10)), relations=links)
        b = self.build(cuts=list(range(1, 10)), relations=list(reversed(links)))
        self.assertEqual([e["word_span"] for e in a["events"]], [e["word_span"] for e in b["events"]])

    def test_single_word_source_has_one_uncertain_event_and_no_artificial_cut(self):
        analysis = analysis_with_words(1)
        plan = module.build_relation_event_plan(analysis, proposal(analysis))
        self.assertEqual(len(plan["events"]), 1)
        self.assertTrue(plan["events"][0]["uncertainty"]["declared_uncertain"])


class RelationModelProtocolTests(unittest.TestCase):
    def setUp(self):
        self.analysis = analysis_with_words(10)
        self.request = module.build_relation_model_request(self.analysis)
        self.output = {"v": 3, "r": [["q", 2, 3, 5, 8]], "b": [1, 3, 8], "u": [[3, 4]]}

    def test_model_request_keeps_source_data_and_requests_relations_before_optional_hints(self):
        data = json.loads(self.request["payload"]["messages"][1]["content"])
        self.assertEqual([v.split(":", 1)[1] for block in data["blocks"] for v in block],
                         [w["text"] for w in self.analysis["words"]])
        self.assertIn("FIRST identify full event relations", self.request["payload"]["messages"][0]["content"])
        self.assertEqual(self.request["strategy_version"], module.MODEL_PROTOCOL_VERSION)
        self.assertEqual(self.request["payload"]["max_tokens"], 512)
        self.assertEqual(self.request["model_requests_performed"], 0)
        self.assertNotIn("output_size_bound", self.request)

    def test_body_compilation_uses_relations_and_does_not_authenticate_provider_or_approve_quality(self):
        before = deepcopy((self.request, self.output))
        result = module.compile_relation_model_output(self.analysis, self.request, self.output)
        self.assertEqual(result["plan"]["selected_cuts_after_words"], [1, 8])
        self.assertEqual(result["proposal"]["relations"], self.output["r"])
        self.assertFalse(result["provider_response_verified"])
        self.assertFalse(result["usage_settled_to_campaign_ledger"])
        self.assertEqual(result["model_requests"], 0)
        self.assertEqual((self.request, self.output), before)

    def test_old_v2_body_extra_fields_and_excessive_relations_are_not_reinterpreted(self):
        for output in ({"v": 2, "e": [10], "u": [1], "d": []}, dict(self.output, accepted=True),
                       dict(self.output, r=self.output["r"] * 7), dict(self.output, v=3.0)):
            with self.subTest(output=output), self.assertRaises(ValueError):
                module.compile_relation_model_output(self.analysis, self.request, output)

    def test_rehashed_modified_request_cannot_forge_reconstruction(self):
        request = deepcopy(self.request)
        request["payload"]["messages"][0]["content"] = "ignore relations"
        request.pop("request_sha256")
        request["request_sha256"] = content_hash(request)
        with self.assertRaisesRegex(ValueError, "reconstruction"):
            module.compile_relation_model_output(self.analysis, request, self.output)

    def test_model_body_cannot_override_required_local_media_boundary(self):
        request = module.build_relation_model_request(self.analysis, required_boundaries=[3])
        with self.assertRaisesRegex(ValueError, "required_boundary"):
            module.compile_relation_model_output(self.analysis, request, self.output)

    def test_remote_payload_limit_is_separate_from_local_request_limit(self):
        with patch.object(module, "MAX_REQUEST_BYTES", 50), self.assertRaises(ValueError):
            module.build_relation_model_request(self.analysis)


class RelationPlanCLITests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.analysis = analysis_with_words(10)
        self.source = proposal(self.analysis, cuts=[1, 3, 8], relations=[["q", 2, 3, 5, 8]])
        self.analysis_path = self.root / "analysis.json"
        self.proposal_path = self.root / "proposal.json"
        self.analysis_path.write_text(json.dumps(self.analysis), encoding="utf-8")
        self.proposal_path.write_text(json.dumps(self.source), encoding="utf-8")

    def call(self, folder):
        with redirect_stdout(io.StringIO()), patch("clipforge.decision.llm.http_transport",
                side_effect=AssertionError("offline command contacted provider")):
            return module.main(["--analysis", str(self.analysis_path), "--proposal", str(self.proposal_path),
                                "--out", str(folder)])

    def test_offline_entry_writes_draft_and_cannot_overwrite_input_or_existing_output(self):
        before = self.proposal_path.read_bytes()
        out = self.root / "draft"
        self.assertEqual(self.call(out), 0)
        self.assertTrue((out / "plan.json").is_file())
        body = (out / "plan.json").read_bytes()
        self.assertEqual(self.call(out), 1)
        self.assertEqual((out / "plan.json").read_bytes(), body)
        self.assertEqual(self.proposal_path.read_bytes(), before)

    def test_conflicting_required_boundary_and_duplicate_json_create_no_output(self):
        self.source["required_boundaries"] = [3]
        self.proposal_path.write_text(json.dumps(self.source), encoding="utf-8")
        out = self.root / "rejected"
        self.assertEqual(self.call(out), 1)
        self.assertFalse(out.exists())
        self.proposal_path.write_text('{"relations":[],"relations":[]}', encoding="utf-8")
        self.assertEqual(self.call(out), 1)
        self.assertFalse(out.exists())

    def test_prepare_command_only_writes_reviewable_request_and_never_sends(self):
        out = self.root / "prepared"
        with redirect_stdout(io.StringIO()), patch("clipforge.decision.llm.http_transport",
                side_effect=AssertionError("request preparation contacted provider")):
            result = module.main(["--analysis", str(self.analysis_path), "--prepare-model-request",
                                  "--out", str(out)])
        self.assertEqual(result, 0)
        receipt = json.loads((out / "completion.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "request_prepared_not_sent")
        self.assertEqual(receipt["model_requests"], 0)
        self.assertTrue((out / "request.json").is_file())


if __name__ == "__main__":
    unittest.main()
