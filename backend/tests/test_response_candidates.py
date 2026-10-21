"""Whole-event candidates through validators, scoring, EDL and offline files."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from clipforge.decision import response_candidates as module
from clipforge.decision.candidates import WindowOptions, content_hash, generate_candidates
from clipforge.decision.generation import LEGACY, settings_snapshot
from clipforge.decision.llm import apply_llm, validate_llm_result
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.validation import validate_candidates
from clipforge.editing.edl import candidates_to_edl, validate_edl
from test_edl import source_fixture
from test_llm_scoring import response, snapshot
from test_relation_event_plan import analysis_with_words, proposal


def fixture(count=8, scale=8):
    analysis = analysis_with_words(count)
    for name in ("words", "sentences", "shots", "speech"):
        for row in analysis[name]:
            row["start"] *= scale
            row["end"] *= scale
    analysis["media"]["duration_seconds"] *= scale
    relations = [["q", 1, 1, 2, 2], ["q", 5, 5, 6, 6]] if count >= 6 else [["q", 1, 1, 2, 2]]
    return analysis, proposal(analysis, cuts=list(range(1, count)), relations=relations)


class ResponseCandidateTests(unittest.TestCase):
    def test_short_seed_and_every_word_hint_cannot_recreate_answer_fragments(self):
        analysis, source = fixture()
        before = deepcopy((analysis, source))
        bundle = module.build_response_candidates(analysis, source)
        windows = bundle["windows"]
        self.assertEqual(windows["candidate_count"], 2)
        self.assertEqual([c["source_sentences"] for c in windows["candidates"]],
                         [["s0", "s1", "s2", "s3"], ["s4", "s5", "s6", "s7"]])
        self.assertEqual(windows["generator"]["version"], module.VERSION)
        self.assertEqual(windows["generator"]["proposal"], source)
        self.assertEqual((analysis, source), before)
        self.assertFalse(bundle["automatically_recommendable"])
        self.assertFalse(bundle["semantic_completeness_verified"])
        self.assertIn("response_review_required", windows["warnings"])
        self.assertIn("response_unresolved_source_end", windows["warnings"])
        validate_candidates(windows, analysis)

    def test_causal_result_and_elaboration_stay_in_one_candidate_before_scoring(self):
        analysis, source = fixture()
        source["relations"].insert(1, ["c", 2, 2, 3, 3])
        windows = module.generate_response_candidates(analysis, source)
        scored = score_candidates(windows, analysis)
        self.assertEqual(scored["candidate_count"], 2)
        by_id = {c["id"]: c for c in scored["candidates"]}
        for old in windows["candidates"]:
            self.assertEqual([by_id[old["id"]][k] for k in ("start", "end", "text", "source_sentences")],
                             [old[k] for k in ("start", "end", "text", "source_sentences")])
        self.assertEqual(scored["windows_sha256"], content_hash(windows))
        validate_scored(scored, analysis)

    def test_score_and_compatible_edl_preserve_explicit_reverse_order_and_all_edges(self):
        analysis, source = fixture()
        windows = module.generate_response_candidates(analysis, source)
        scored = score_candidates(windows, analysis)
        ids = [c["id"] for c in reversed(windows["candidates"])]
        measured = source_fixture(sha256=analysis["media"]["normalized_sha256"],
                                  total_frames=round(analysis["media"]["duration_seconds"]*30))
        edl = candidates_to_edl(scored, ids, measured)
        validate_edl(edl)
        self.assertEqual([c["provenance"]["candidate_id"] for c in edl["clips"]], ids)
        self.assertEqual([(c["source_in_frame"], c["source_out_frame"]) for c in edl["clips"]], [(984, 1872), (24, 912)])
        self.assertEqual(edl["duration_frames"], 1776)
        self.assertEqual(edl["clips"][0]["provenance"]["source_sentences"], ["s4", "s5", "s6", "s7"])

    def test_existing_llm_contract_accepts_new_generator_without_real_transport(self):
        analysis, source = fixture()
        windows = module.generate_response_candidates(analysis, source)
        scored = score_candidates(windows, analysis)
        transport = Mock(side_effect=[response(60), response(70)])
        with patch("clipforge.decision.llm.http_transport", side_effect=AssertionError("unexpected network")):
            result = apply_llm(scored, analysis, snapshot(), api_key="fake-test-key", transport=transport, limiter=Mock())
            validate_llm_result(result, analysis)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(result["generator"], windows["generator"])
        self.assertEqual(sorted((c["start"], c["end"]) for c in result["candidates"]),
                         [(c["start"], c["end"]) for c in windows["candidates"]])

    def test_valid_sentence_window_with_recomputed_id_still_cannot_shorten_an_event(self):
        analysis, source = fixture()
        forged = module.generate_response_candidates(analysis, source)
        candidate = forged["candidates"][0]
        members = analysis["sentences"][:3]
        refs = [s["id"] for s in members]
        candidate.update(end=members[-1]["end"], duration_seconds=round(members[-1]["end"]-members[0]["start"], 9),
                         source_sentences=refs, text="\n".join(s["text"] for s in members))
        candidate["id"] = "c_"+content_hash({"analysis": forged["analysis_sha256"], "version": module.VERSION,
                                            "source_sentences": refs})
        with self.assertRaisesRegex(ValueError, "完整事件生成"):
            validate_candidates(forged, analysis)

    def test_omitting_event_or_unresolved_warning_is_detected_by_reconstruction(self):
        analysis, source = fixture()
        original = module.generate_response_candidates(analysis, source)
        for kind in ("event", "warning", "proposal"):
            bad = deepcopy(original)
            if kind == "event":
                bad["candidates"].pop()
                bad["candidate_count"] -= 1
            elif kind == "warning":
                bad["warnings"] = []
            else:
                bad["generator"]["proposal"]["normalized_sha256"] = "e"*64
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_candidates(bad, analysis)

    def test_overlong_or_gapped_event_is_audited_and_never_split_to_fit(self):
        analysis, _ = fixture(count=8, scale=15)
        source = proposal(analysis, relations=[["q", 1, 1, 2, 2]])
        long = module.build_response_candidates(analysis, source, WindowOptions(max_gap_seconds=6))
        self.assertEqual(long["windows"]["candidate_count"], 0)
        self.assertEqual(len(long["event_audit"]), 1)
        self.assertIn("whole_event_exceeds_maximum_duration", long["event_audit"][0]["reasons"])
        analysis, source = fixture()
        gap = module.build_response_candidates(analysis, source, WindowOptions(max_gap_seconds=.1))
        self.assertEqual(gap["windows"]["candidate_count"], 0)
        self.assertTrue(all("whole_event_gap_exceeds_candidate_contract" in r["reasons"] for r in gap["event_audit"]))
        validate_candidates(gap["windows"], analysis)

    def test_music_example_beyond_last_word_is_retained_in_audit_not_lost_in_export(self):
        analysis, source = fixture()
        evidence = {"analysis_sha256": content_hash(analysis),
                    "normalized_sha256": analysis["media"]["normalized_sha256"],
                    "annotation_sha256": "c"*64, "start_seconds": 30.4, "end_seconds": 32.8,
                    "kind": "same_event_example_no_dialogue"}
        source["non_dialogue_evidence"] = [evidence]
        bundle = module.build_response_candidates(analysis, source)
        retained = [e for r in bundle["event_audit"] for e in r["retained_non_dialogue_examples"]]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["evidence"], evidence)
        self.assertEqual(bundle["windows"]["candidate_count"], 1)
        self.assertIn("non_dialogue_extension_requires_explicit_media_range", bundle["event_audit"][0]["reasons"])
        self.assertFalse(bundle["automatically_recommendable"])

    def test_explicit_candidate_cap_is_not_a_top_k_truncation(self):
        analysis, source = fixture()
        with self.assertRaisesRegex(ValueError, "max_candidates"):
            module.build_response_candidates(analysis, source, WindowOptions(max_candidates=1))

    def test_unrelated_prefix_and_unlinked_source_are_audited_not_used_as_events(self):
        analysis, source = fixture()
        source["relations"] = [["q", 5, 5, 6, 6]]
        bundle = module.build_response_candidates(analysis, source)
        self.assertEqual(bundle["windows"]["candidate_count"], 1)
        self.assertIn("event_has_no_declared_relation_support", bundle["event_audit"][0]["reasons"])
        source["relations"] = []
        self.assertEqual(module.generate_response_candidates(analysis, source)["candidate_count"], 0)

    def test_required_interruption_cannot_become_a_complete_answer_candidate(self):
        analysis, source = fixture()
        source["relations"] = [["q", 1, 1, 2, 2]]
        source["required_boundaries"] = [4]
        bundle = module.build_response_candidates(analysis, source)
        self.assertEqual(bundle["windows"]["candidate_count"], 0)
        self.assertIn("response_interrupted_by_required_boundary", bundle["event_audit"][0]["reasons"])
        validate_candidates(bundle["windows"], analysis)

    def test_invalid_sentence_clock_is_rejected_before_candidate_generation(self):
        analysis, source = fixture()
        analysis["sentences"][3]["end"] += .1
        source["analysis_sha256"] = content_hash(analysis)
        with self.assertRaisesRegex(ValueError, "句子边界"):
            module.build_response_candidates(analysis, source)

    def test_strategy_remains_explicit_with_existing_default_contract_unchanged(self):
        analysis, _ = fixture()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings_snapshot()["version"], LEGACY)
        validate_candidates(generate_candidates(analysis), analysis)
        with patch.dict(os.environ, {"CLIPFORGE_CANDIDATE_GENERATOR": module.VERSION}):
            with self.assertRaises(ValueError):
                settings_snapshot()

    def test_local_cli_writes_complete_audit_without_network_or_overwriting_inputs(self):
        analysis, source = fixture()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            a, p, out = root/"analysis.json", root/"proposal.json", root/"generated"
            a.write_text(json.dumps(analysis), encoding="utf-8")
            p.write_text(json.dumps(source), encoding="utf-8")
            original = (a.read_bytes(), p.read_bytes())
            args = ["--analysis", str(a), "--proposal", str(p), "--out", str(out)]
            with redirect_stdout(io.StringIO()), patch("clipforge.decision.llm.http_transport", side_effect=AssertionError("network")):
                self.assertEqual(module.main(args), 0)
                self.assertEqual(module.main(args), 1)
                self.assertEqual((a.read_bytes(), p.read_bytes()), original)
                source["relations"] = [["q", 1, 1, 2, 7], ["q", 5, 5, 6, 6]]
                p.write_text(json.dumps(source), encoding="utf-8")
                self.assertEqual(module.main(args[:-1]+[str(root/"invalid")]), 1)
                self.assertFalse((root/"invalid").exists())
            receipt = json.loads((out/"completion.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["candidate_count"], 2)
            self.assertEqual(receipt["network_requests"], 0)


if __name__ == "__main__":
    unittest.main()
