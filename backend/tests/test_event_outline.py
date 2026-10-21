"""Source/structure invariants, deliberately no asserted semantic success."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from clipforge.decision import event_outline as module
from clipforge.decision.event_outline import build_event_outline, verify_event_outline
from clipforge.editing.word_spans import content_hash


def fixture():
    texts = ["旧事", "你很熟练", "还行吧", "怎么说呢", "好的", "那我们接下来", "做另一个任务"]
    words = [{"id": f"w{i}", "start": i + .1, "end": i + .8,
              "text": text, "probability": .9} for i, text in enumerate(texts)]
    groups = [(0, 1), (1, 2), (2, 7)]
    sentences = [{"id": f"s{i}", "start": words[a]["start"], "end": words[b - 1]["end"],
                  "text": "".join(row["text"] for row in words[a:b]),
                  "word_ids": [row["id"] for row in words[a:b]]}
                 for i, (a, b) in enumerate(groups)]
    analyzer = {"status": "ok", "tool": "fixture", "version": "1", "model": None, "parameters": {}}
    analysis = {"schema_version": "0.1.0", "result_kind": "synthetic_example",
                "media": {"source_sha256": "a" * 64, "normalized_sha256": "b" * 64,
                          "duration_seconds": 8, "time_reference": "normalized_video", "has_audio": True},
                "analyzers": {name: deepcopy(analyzer) for name in ("shots", "vad", "asr")},
                "shots": [{"start": 0, "end": 8}], "speech": [{"start": 0, "end": 8}],
                "silence": [], "words": words, "sentences": sentences}

    def role(identifier, kind, first, last):
        return {"id": identifier, "role": kind, "first_word_id": f"w{first}", "last_word_id": f"w{last}"}

    specification = {"schema_version": "0.1.0-research.1", "artifact_kind": "event_outline_specification",
                     "analysis_sha256": content_hash(analysis), "normalized_sha256": "b" * 64,
                     "producer": "Codex", "events": [
                         {"id": "previous", "roles": [role("old", "unclassified", 0, 0)], "relations": []},
                         {"id": "interaction", "roles": [role("appraisal", "elicitation", 1, 1),
                                                            role("reply", "response", 2, 3),
                                                            role("closure", "bridge", 4, 4)],
                          "relations": [{"kind": "implicit_appraisal_response", "from_role": "appraisal", "to_role": "reply"}]},
                         {"id": "next", "roles": [role("setup", "transition_setup", 5, 6)], "relations": []}]}
    return analysis, specification


class EventOutlineTests(unittest.TestCase):
    def test_exact_coverage_partial_ownership_and_no_mutation_or_semantic_approval(self):
        analysis, spec = fixture()
        before = deepcopy((analysis, spec))
        result = build_event_outline(analysis, spec)
        self.assertEqual((analysis, spec), before)
        self.assertEqual(result, build_event_outline(analysis, spec))
        self.assertTrue(verify_event_outline(result, analysis, spec))
        reply = result["events"][1]["roles"][1]
        self.assertEqual(reply["source_words"], ["w2", "w3"])
        self.assertEqual(reply["text"], "还行吧怎么说呢")
        self.assertEqual(reply["sentence_ownership"], [{"sentence_id": "s2", "selected_word_ids": ["w2", "w3"],
                                                      "all_sentence_words_selected": False}])
        self.assertEqual(reply["word_span"], {"first_word_id": "w2", "last_word_id": "w3", "start_seconds": 2.1, "end_seconds": 3.8})
        self.assertTrue(result["events"][0]["sentence_ownership"][0]["all_sentence_words_selected"])
        rows = [result] + result["events"]
        rows += [role for event in result["events"] for role in event["roles"]]
        rows += [relation for event in result["events"] for relation in event["relations"]]
        for row in rows:
            self.assertEqual(row["judgement_status"], "semantic_hypothesis_not_verified")
            for name in ("semantic_completeness_verified", "automatically_recommendable", "production_candidate"):
                self.assertIs(row[name], False)
        self.assertIs(result["frame_boundary_verified"], False)

    def test_rehashed_tampering_with_text_roles_relations_flags_or_spec_is_rejected(self):
        analysis, spec = fixture()
        original = build_event_outline(analysis, spec)
        mutations = [lambda r: r["events"][1]["roles"][1].update(text="invented"),
                     lambda r: r["events"][1]["roles"][1].update(role="context"),
                     lambda r: r["events"][1]["relations"][0].update(to_role="closure"),
                     lambda r: r.update(semantic_completeness_verified=True),
                     lambda r: r["specification"].update(producer="reviewer")]
        for mutate in mutations:
            result = deepcopy(original)
            mutate(result)
            result.pop("outline_sha256")
            result["outline_sha256"] = content_hash(result)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                verify_event_outline(result, analysis, spec)

    def test_source_identity_including_unselected_metadata_is_bound(self):
        analysis, spec = fixture()
        result = build_event_outline(analysis, spec)
        analysis["words"][0]["probability"] = .8
        with self.assertRaisesRegex(ValueError, "source identity"):
            build_event_outline(analysis, spec)
        spec["analysis_sha256"] = content_hash(analysis)
        with self.assertRaises(ValueError):
            verify_event_outline(result, analysis, spec)
        spec["normalized_sha256"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "source identity"):
            build_event_outline(analysis, spec)

    def test_roles_must_partition_words_and_ids_must_be_unique(self):
        for field, value in (("first_word_id", "w1"), ("first_word_id", "w3"),
                             ("last_word_id", "w1"), ("last_word_id", "absent"),
                             ("id", "appraisal"), ("role", "approved_event")):
            analysis, spec = fixture()
            spec["events"][1]["roles"][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_event_outline(analysis, spec)
        analysis, spec = fixture()
        spec["events"].pop()
        with self.assertRaisesRegex(ValueError, "uncovered"):
            build_event_outline(analysis, spec)
        analysis, spec = fixture()
        spec["events"][2]["id"] = "previous"
        with self.assertRaisesRegex(ValueError, "duplicate event"):
            build_event_outline(analysis, spec)

    def test_relation_must_be_forward_local_supported_and_not_duplicated(self):
        for field, value in (("kind", "semantic_pass"), ("from_role", "reply"),
                             ("to_role", "appraisal"), ("to_role", "setup"), ("to_role", True)):
            analysis, spec = fixture()
            spec["events"][1]["relations"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_event_outline(analysis, spec)
        analysis, spec = fixture()
        spec["events"][1]["relations"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate research relation"):
            build_event_outline(analysis, spec)

    def test_unknown_fields_including_text_and_approval_are_never_accepted(self):
        for level, field, value in (("spec", "approved", True), ("event", "complete", True),
                                    ("role", "text", "replacement"), ("relation", "verified", True)):
            analysis, spec = fixture()
            target = {"spec": spec, "event": spec["events"][1], "role": spec["events"][1]["roles"][1],
                      "relation": spec["events"][1]["relations"][0]}[level]
            target[field] = value
            with self.subTest(level=level), self.assertRaisesRegex(ValueError, "exactly"):
                build_event_outline(analysis, spec)

    def test_nan_infinity_bool_and_invalid_json_fail_clearly(self):
        for value in (float("nan"), float("inf"), True):
            analysis, spec = fixture()
            analysis["words"][1]["start"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_event_outline(analysis, spec)
        for value in (True, float("nan"), ["w3"]):
            analysis, spec = fixture()
            spec["events"][1]["roles"][1]["last_word_id"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_event_outline(analysis, spec)

    def test_fixed_limits_fail_whole_call_instead_of_truncating(self):
        for limit, value in (("MAX_WORDS", 6), ("MAX_SENTENCES", 2), ("MAX_EVENTS", 2),
                             ("MAX_ROLES", 4), ("MAX_RELATIONS", 0), ("MAX_JSON_BYTES", 100),
                             ("MAX_JSON_NODES", 5), ("MAX_JSON_DEPTH", 1),
                             ("MAX_TEXT_CHARACTERS", 3), ("MAX_ID_CHARACTERS", 2)):
            analysis, spec = fixture()
            with self.subTest(limit=limit), patch.object(module, limit, value), self.assertRaises(ValueError):
                build_event_outline(analysis, spec)
        analysis, spec = fixture()
        size = max(len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
                   for item in (analysis, spec)) + 100
        with patch.object(module, "MAX_JSON_BYTES", size), self.assertRaisesRegex(ValueError, "outline exceeds"):
            build_event_outline(analysis, spec)


if __name__ == "__main__":
    unittest.main()
