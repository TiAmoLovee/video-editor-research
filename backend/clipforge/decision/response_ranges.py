"""Conservative whole-response scopes over immutable ASR ownership blocks.

A relation's short right-hand span is an answer seed, never an end-of-answer
certificate. Response scope stays open through subsequent blocks until a new
supplied question, explicit topic transition or required boundary. Possible
text questions are warnings, not inferred speaker changes. Source exhaustion
is always unresolved. This text-only draft cannot certify event completeness.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import unicodedata

from clipforge.decision import event_continuity as discourse
from clipforge.decision import event_dependencies as surface
from clipforge.decision.model_event_plan import _bounded, _unapproved
from clipforge.decision.relation_event_plan import (
    MAX_ANALYSIS_BYTES, MAX_LOCAL_BYTES, MAX_PLAN_BYTES, _read, _validate,
    build_relation_event_plan,
)
from clipforge.editing.word_spans import content_hash


VERSION = "whole-response-ownership-blocks-v1"
ACKNOWLEDGMENTS = frozenset({"好", "好的", "嗯", "是的", "对", "没错", "ok", "okay"})


def _blocks(analysis, words):
    positions = {word["id"]: i for i, word in enumerate(words, 1)}
    blocks, owners, quotation_stack = [], {}, []
    character = 0
    masked_parts, word_characters = [], {}
    for number, sentence in enumerate(analysis["sentences"]):
        indices = [positions[identifier] for identifier in sentence["word_ids"]]
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError("response_blocks_require_contiguous_original_words")
        text = "".join(words[i - 1]["text"] for i in indices)
        masked = discourse._mask_quotes(text, quotation_stack)
        for i in indices:
            owners[i] = number
            end = character + len(words[i - 1]["text"])
            word_characters[i] = (character, end)
            character = end
        masked_parts.append(masked)
        blocks.append({"index": number, "source_sentence_id": sentence["id"],
                       "first_word": indices[0], "last_word": indices[-1],
                       "masked_text": masked})
    if list(owners) != list(range(1, len(words) + 1)):
        raise ValueError("response_blocks_must_cover_source_in_order_once")
    return blocks, owners, "".join(masked_parts), word_characters


def _possible_question(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    if surface._question_kind(normalized.rstrip("”’\"'）)】]」』")):
        return True
    intent = discourse._INTENT.search(normalized)
    return bool(intent and not discourse._NEGATED_INTENT.search(normalized[:intent.start()])
                and not discourse._SETTLED.search(intent.group("target"))
                and not discourse._META.match(intent.group("target").lstrip()))


def _topic_transition(text):
    normalized = unicodedata.normalize("NFKC", text).strip()
    while normalized:
        if discourse._NEW_TOPIC.match(normalized):
            return True
        # A neutral acknowledgment alone is not a boundary. It can precede
        # an independently recognizable topic-switch construction.
        head, comma, rest = normalized.partition(",")
        if not comma or head.strip().casefold() not in ACKNOWLEDGMENTS:
            return False
        normalized = rest.lstrip()
    return False


def recognize_response_ranges(analysis, proposal):
    """Derive new local scopes without changing or accepting the original proposal.

    Optional word-boundary hints are deliberately not answer-closure evidence.
    If trusted structural boundaries contradict a supplied relation, refuse the
    draft rather than inventing a shorter answer or absorbing the next topic.
    """
    words, _ = _validate(analysis, proposal)
    blocks, owners, masked_source, characters = _blocks(analysis, words)
    relations = proposal["relations"]
    starts = {}

    def anchor(first, kind, relation_index=None):
        row = starts.setdefault(first, {"first_word": first, "kinds": [], "question_seed_indices": []})
        if kind not in row["kinds"]:
            row["kinds"].append(kind)
        if relation_index is not None:
            row["question_seed_indices"].append(relation_index)

    question_cues, quoted_questions = [], []
    for block in blocks:
        if _possible_question(block["masked_text"]):
            question_cues.append({"first_word": block["first_word"],
                                  "source_sentence_id": block["source_sentence_id"],
                                  "speaker_change_verified": False})
        if _topic_transition(block["masked_text"]):
            anchor(block["first_word"], "explicit_topic_transition_cue")
    for index, row in enumerate(relations):
        if row[0] != "q":
            continue
        visible = masked_source[characters[row[1]][0]:characters[row[2]][1]].strip()
        if not visible:
            quoted_questions.append(index)
            continue
        anchor(blocks[owners[row[1]]]["first_word"], "supplied_question_hypothesis", index)
    for cut in proposal["required_boundaries"]:
        if blocks[owners[cut]]["last_word"] != cut:
            raise ValueError("required_boundary_inside_response_ownership_block")
        anchor(cut + 1, "required_boundary")
    barriers = [starts[first] for first in sorted(starts)]

    def span(first, last):
        selected = words[first - 1:last]
        return {"first_word_index": first, "last_word_index": last,
                "first_word_id": selected[0]["id"], "last_word_id": selected[-1]["id"],
                "source_words": [w["id"] for w in selected],
                "text": "".join(w["text"] for w in selected),
                "start_seconds": min(w["start"] for w in selected),
                "end_seconds": max(w["end"] for w in selected)}

    resolved, expanded_relations = [], []
    for index, relation in enumerate(relations):
        kind, a, b, c, d = relation
        first = blocks[owners[a]]["first_word"]
        # Preserve a response already beginning inside a mixed ASR block. When
        # it begins later, keep every intervening block, including lead-ins.
        left_end = min(blocks[owners[b]]["last_word"], c - 1)
        reply_first = left_end + 1
        following = next((row for row in barriers if row["first_word"] > first), None)
        if following and following["first_word"] <= d:
            raise ValueError("declared_relation_crosses_new_question_or_required_transition")
        last = following["first_word"] - 1 if following else len(words)
        end_reason = ("interrupted_by_required_boundary" if following and "required_boundary" in following["kinds"]
                      else "next_structural_turn_cue" if following else "unresolved_source_end")
        expanded = [kind, first, left_end, reply_first, last]
        if expanded not in expanded_relations:
            expanded_relations.append(expanded)
        possible_inside = [deepcopy(row) for row in question_cues
                           if reply_first <= row["first_word"] <= last
                           and row["first_word"] not in starts]
        resolved.append({"source_relation_index": index, "kind": kind,
            "original_relation_indices": deepcopy(relation), "expanded_relation_index": expanded_relations.index(expanded),
            "antecedent_span": span(first, left_end), "response_seed_span": span(c, d),
            "response_span": span(reply_first, last), "protected_envelope": span(first, last),
            "response_start_policy": "include_intervening_original_blocks_not_only_model_seed",
            "mixed_question_response_block": owners[b] == owners[c],
            "endpoint": {"reason": end_reason,
                         "next_boundary_evidence": deepcopy(following),
                         "source_end_is_not_semantic_closure": following is None,
                         "speaker_change_verified": False, "semantic_closure_verified": False},
            "possible_unverified_questions_inside_response": possible_inside,
            "question_seed_wholly_quoted": index in quoted_questions, **_unapproved()})

    expanded = deepcopy(proposal)
    expanded["origin"] = {"kind": "offline_replay", "reference_sha256": content_hash(proposal)}
    expanded["relations"] = expanded_relations
    expanded["boundary_hints"] = [row["first_word"] - 1 for row in barriers if row["first_word"] > 1]
    # No automatic semantic approval follows from role seeds or a text cue.
    expanded["uncertain_word_ranges"] = [[1, len(words)]]
    plan = build_relation_event_plan(analysis, expanded)
    body = {"schema_version": "0.1.0-research.1", "artifact_kind": "whole_response_range_report",
        "strategy_version": VERSION, "producer": {"kind": "deterministic_local_response_scope_recognizer"},
        "analysis_sha256": content_hash(analysis), "normalized_sha256": analysis["media"]["normalized_sha256"],
        "source_proposal_sha256": content_hash(proposal), "source_origin": deepcopy(proposal["origin"]),
        "original_boundary_hints_used_as_answer_endings": False,
        "original_proposal_modified_or_reclassified": False,
        "source_blocks": [{k: v for k, v in block.items() if k != "masked_text"} for block in blocks],
        "structural_turn_cues": barriers, "possible_text_question_cues": question_cues,
        "quoted_question_seed_indices": quoted_questions,
        "response_ranges": resolved, "expanded_proposal": expanded, "generated_plan": plan,
        "limits": {"unknown_speakers": True, "role_hypotheses_not_speaker_diarization": True,
                   "source_end_requires_review": True, "overlong_response_is_not_split_to_fit": True,
                   "unreported_relations_may_remain_unresolved": True},
        "model_requests": 0, "media_operations": 0, "historical_labels_changed": False, **_unapproved()}
    _bounded(body, MAX_PLAN_BYTES, "whole response report")
    body["report_sha256"] = content_hash(body)
    return body


def verify_response_ranges(report, analysis, proposal):
    _bounded(report, MAX_PLAN_BYTES, "whole response report")
    if content_hash(report) != content_hash(recognize_response_ranges(analysis, proposal)):
        raise ValueError("response_report_reconstruction_mismatch")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError("output_directory_exists")
        report = recognize_response_ranges(_read(args.analysis, MAX_ANALYSIS_BYTES),
                                            _read(args.proposal, MAX_LOCAL_BYTES))
        files = {"response-ranges.json": report, "plan.json": report["generated_plan"]}
        status = {"status": "conservative_response_draft_not_semantically_verified",
                  "response_ranges": len(report["response_ranges"]), "model_requests": 0,
                  "production_changed": False}
        bodies = {name: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                  for name, value in files.items()}
        bodies["completion.json"] = json.dumps(status) + "\n"
        args.out.mkdir(parents=True, exist_ok=False)
        for name, body in bodies.items():
            with (args.out / name).open("x", encoding="utf-8", newline="\n") as target:
                target.write(body)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "rejected", "model_requests": 0, "production_changed": False}))
        return 1
    print(json.dumps(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
