"""Bridge supplied response hypotheses to existing candidate/scoring contracts.

Only whole generated events become windows. There is no sliding-window search,
new model transport, semantic approval, or production strategy switch here.
Events that the sentence-based contract cannot faithfully represent remain in
the audit with explicit reasons; they are never shortened or silently repaired.
"""

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

from clipforge.decision.candidates import WindowOptions, content_hash
from clipforge.decision.model_event_plan import _unapproved
from clipforge.decision.relation_event_plan import MAX_ANALYSIS_BYTES, MAX_LOCAL_BYTES, _read
from clipforge.decision.response_ranges import recognize_response_ranges


VERSION = "whole-response-candidates-v1"
WARNINGS = ("response_review_required", "response_event_skipped",
            "response_unresolved_source_end", "response_no_eligible_event")


def build_response_candidates(analysis, proposal, options=None):
    options = WindowOptions() if options is None else options
    if not isinstance(options, WindowOptions):
        raise ValueError("response candidate options must be WindowOptions")
    report = recognize_response_ranges(analysis, proposal)
    candidates, audit = [], []
    for event in report["generated_plan"]["events"]:
        word_span = event["word_span"]
        selected = set(word_span["source_words"])
        members = [s for s in analysis["sentences"] if selected.intersection(s["word_ids"])]
        reasons = []
        if [w for s in members for w in s["word_ids"]] != word_span["source_words"]:
            reasons.append("event_not_representable_as_whole_source_sentences")
        start = min(s["start"] for s in members)
        end = max(s["end"] for s in members)
        duration = round(end - start, 9)
        if not event["relation_indices"]:
            reasons.append("event_has_no_declared_relation_support")
        if start != word_span["start_seconds"] or end != word_span["end_seconds"]:
            reasons.append("sentence_timing_changes_protected_word_envelope")
        for response in report["response_ranges"]:
            protected = response["protected_envelope"]
            if (word_span["first_word_index"] <= protected["first_word_index"]
                    and protected["last_word_index"] <= word_span["last_word_index"]
                    and response["endpoint"]["reason"] == "interrupted_by_required_boundary"):
                reasons.append("response_interrupted_by_required_boundary")
                break
        if duration < options.min_seconds:
            reasons.append("whole_event_below_minimum_duration")
        if duration > options.max_seconds:
            reasons.append("whole_event_exceeds_maximum_duration")
        if event["event_extent"]["non_dialogue_extension_beyond_last_word"]:
            reasons.append("non_dialogue_extension_requires_explicit_media_range")
        references = [s["id"] for s in members]
        if any(s["id"] not in references and s["start"] < end and s["end"] > start
               for s in analysis["sentences"]):
            reasons.append("overlapping_unselected_sentence")
        cursor = members[0]["end"]
        for sentence in members[1:]:
            if sentence["start"] - cursor > options.max_gap_seconds:
                reasons.append("whole_event_gap_exceeds_candidate_contract")
                break
            cursor = max(cursor, sentence["end"])
        candidate_id = None
        if not reasons:
            if len(candidates) >= options.max_candidates:
                raise ValueError("response candidates exceed explicit max_candidates; no truncation")
            key = {"analysis": report["analysis_sha256"], "version": VERSION,
                   "source_sentences": references}
            candidate_id = "c_" + content_hash(key)
            candidates.append({"id": candidate_id, "start": start, "end": end,
                "duration_seconds": duration, "text": "\n".join(s["text"] for s in members),
                "source_sentences": references, "scoring_status": "pending", "score": None,
                "reasons": [], "scorer": None})
        audit.append({"event_id": event["id"], "candidate_id": candidate_id,
            "source_word_ids": deepcopy(word_span["source_words"]),
            "event_extent": deepcopy(event["event_extent"]),
            "retained_non_dialogue_examples": deepcopy(event["retained_non_dialogue_examples"]),
            "status": "candidate_requires_review" if candidate_id else "not_representable_without_change",
            "reasons": reasons, **_unapproved()})
    warnings = [WARNINGS[0]]
    if any(row["candidate_id"] is None for row in audit):
        warnings.append(WARNINGS[1])
    if any(row["endpoint"]["source_end_is_not_semantic_closure"] for row in report["response_ranges"]):
        warnings.append(WARNINGS[2])
    if not candidates:
        warnings.append(WARNINGS[3])
    windows = {"schema_version": "0.1.0-draft.1", "artifact_kind": "candidate_windows",
        "stage": "generated", "input_result_kind": analysis["result_kind"],
        "analysis_sha256": report["analysis_sha256"], "media": deepcopy(analysis["media"]),
        "generator": {"version": VERSION, "options": asdict(options), "proposal": deepcopy(proposal)},
        "candidate_count": len(candidates), "candidates": candidates, "warnings": warnings}
    result = {"artifact_kind": "response_candidate_bridge", "strategy_version": VERSION,
        "source_proposal_sha256": content_hash(proposal), "response_report_sha256": report["report_sha256"],
        "windows_sha256": content_hash(windows), "windows": windows,
        "response_ranges": report, "event_audit": audit,
        "network_requests": 0, "production_strategy_changed": False, **_unapproved()}
    result["bridge_sha256"] = content_hash(result)
    return result


def generate_response_candidates(analysis, proposal, options=None):
    return build_response_candidates(analysis, proposal, options)["windows"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError("output_directory_exists")
        analysis = _read(args.analysis, MAX_ANALYSIS_BYTES)
        proposal = _read(args.proposal, MAX_LOCAL_BYTES)
        bundle = build_response_candidates(analysis, proposal)
        from clipforge.decision.validation import validate_candidates
        validate_candidates(bundle["windows"], analysis)
        files = {"candidate_windows.json": bundle["windows"], "response-candidate-audit.json": bundle}
        # Prepare bytes before creating any output, then completion goes last.
        bodies = {name: json.dumps(body, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                  for name, body in files.items()}
        completion = {"status": "unreviewed_event_candidates_generated", "candidate_count": bundle["windows"]["candidate_count"],
                      "bridge_sha256": bundle["bridge_sha256"], "network_requests": 0, "production_changed": False}
        bodies["completion.json"] = json.dumps(completion) + "\n"
        args.out.mkdir(parents=True, exist_ok=False)
        for name, body in bodies.items():
            with (args.out/name).open("x", encoding="utf-8", newline="\n") as outgoing:
                outgoing.write(body)
    except (OSError, ValueError, TypeError, RecursionError):
        print(json.dumps({"status": "rejected", "network_requests": 0, "production_changed": False}))
        return 1
    print(json.dumps(completion))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
