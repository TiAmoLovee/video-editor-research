"""Validate a source-bound, explicitly unapproved semantic event draft.

The caller supplies the event/role interpretation. This module neither infers
it nor verifies its meaning: hashes and complete word ownership demonstrate
provenance and structure only. An outline is not an automatic candidate, an
EDL, a media boundary approval, or a semantic quality measurement.
"""

from copy import deepcopy
import json
import math
import re

from clipforge.analysis.validation import validate_analysis
from clipforge.decision.candidates import ROOT
from clipforge.editing.word_spans import content_hash


VERSION = "source-bound-event-outline-research-v1"
MAX_WORDS = 100000
MAX_SENTENCES = 10000
MAX_EVENTS = 10000
MAX_ROLES = 20000
MAX_RELATIONS = 20000
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_NODES = 1000000
MAX_JSON_DEPTH = 64
MAX_TEXT_CHARACTERS = 1000000
MAX_ID_CHARACTERS = 256
ROLES = frozenset({"context", "elicitation", "response", "supporting_example",
                   "transition_setup", "bridge", "unclassified"})
RELATIONS = frozenset({"question_response", "implicit_appraisal_response",
                       "cause_effect", "example_support"})
SPEC_KEYS = {"schema_version", "artifact_kind", "analysis_sha256",
             "normalized_sha256", "producer", "events"}
EVENT_KEYS = {"id", "roles", "relations"}
ROLE_KEYS = {"id", "role", "first_word_id", "last_word_id"}
RELATION_KEYS = {"kind", "from_role", "to_role"}


def _bounded_json(value, label):
    """Reject invalid/oversized JSON before recursive validation or hashing."""
    pending, count, characters = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError(label + " exceeds fixed JSON node/depth limits; no truncation")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError(label + " requires string JSON object keys")
            characters += sum(len(key) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            characters += len(item)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError(label + " contains NaN/Infinity")
        elif item is not None and type(item) not in (bool, int):
            raise ValueError(label + " contains a non-JSON value")
        if characters > MAX_JSON_BYTES:
            raise ValueError(label + " exceeds fixed JSON size limit; no truncation")
    size = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
            size += len(chunk.encode("utf-8"))
            if size > MAX_JSON_BYTES:
                raise ValueError(label + " exceeds fixed JSON byte limit; no truncation")
    except (UnicodeError, OverflowError) as error:
        raise ValueError(label + " contains an invalid JSON string/number") from error


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(label + " requires exactly the documented fields")


def _identifier(value, label):
    if (not isinstance(value, str) or not value.strip()
            or len(value) > MAX_ID_CHARACTERS):
        raise ValueError(label + " must be a nonempty bounded string ID, not bool")
    return value


def _sha(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(label + " must be a SHA-256 hex digest")
    return value


def _number(value, label):
    if (type(value) not in (int, float)
            or (type(value) is float and not math.isfinite(value)) or value < 0):
        raise ValueError(label + " must be a finite nonnegative number, not bool")


def _analysis(analysis):
    _bounded_json(analysis, "analysis")
    if not isinstance(analysis, dict):
        raise ValueError("analysis must be an original analysis JSON object")
    words, sentences = analysis.get("words"), analysis.get("sentences")
    if not isinstance(words, list) or not 1 <= len(words) <= MAX_WORDS:
        raise ValueError("analysis requires nonempty words within fixed limit; no truncation")
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= MAX_SENTENCES:
        raise ValueError("analysis requires nonempty sentences within fixed limit; no truncation")
    text_count = 0
    for label, rows in (("word", words), ("sentence", sentences)):
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise ValueError(label + " must contain original text")
            text_count += len(row["text"])
            if text_count > MAX_TEXT_CHARACTERS:
                raise ValueError("analysis exceeds fixed text limit; no truncation")
            _identifier(row.get("id"), label + " id")
            _number(row.get("start"), label + " start")
            _number(row.get("end"), label + " end")
            if label == "word" and row.get("probability") is not None:
                _number(row["probability"], "word probability")
    media = analysis.get("media")
    if not isinstance(media, dict):
        raise ValueError("analysis requires original normalized media identity")
    _number(media.get("duration_seconds"), "media duration")
    _sha(media.get("normalized_sha256"), "analysis normalized_sha256")
    schema = json.loads((ROOT / "schemas/analysis.schema.json").read_text(encoding="utf-8"))
    validate_analysis(analysis, schema)
    owners = []
    for sentence in sentences:
        owners.extend((sentence, index) for index in range(len(sentence["word_ids"])))
    return words, owners


def _unapproved():
    return {"judgement_status": "semantic_hypothesis_not_verified",
            "semantic_completeness_verified": False,
            "automatically_recommendable": False, "production_candidate": False}


def build_event_outline(analysis, specification):
    """Bind supplied roles to every original word exactly once, in order.

    Specification: strict top-level identity/producer/events fields; each event
    has id/roles/relations, each role id/role/first_word_id/last_word_id, and
    each relation kind/from_role/to_role. Roles and events partition the source
    word sequence; relations point forward within one event. Unknown fields,
    including approval claims or replacement text, fail the whole operation.
    """
    words, owners = _analysis(analysis)
    _bounded_json(specification, "specification")
    _keys(specification, SPEC_KEYS, "specification")
    if (specification["schema_version"] != "0.1.0-research.1"
            or specification["artifact_kind"] != "event_outline_specification"
            or specification["producer"] != "Codex"):
        raise ValueError("requires the research specification and explicit Codex producer declaration")
    analysis_hash = content_hash(analysis)
    if (_sha(specification["analysis_sha256"], "specification analysis_sha256") != analysis_hash
            or _sha(specification["normalized_sha256"], "specification normalized_sha256")
            != analysis["media"]["normalized_sha256"]):
        raise ValueError("specification source identity differs from original analysis/media")
    events = specification["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError("events must be a nonempty array within fixed limit; no truncation")
    indices = {word["id"]: index for index, word in enumerate(words)}
    event_ids, role_ids = set(), set()
    cursor = role_count = relation_count = 0
    result_events = []

    def describe(first, last):
        selected = words[first:last + 1]
        ownership = []
        for index in range(first, last + 1):
            sentence, _ = owners[index]
            if not ownership or ownership[-1]["sentence_id"] != sentence["id"]:
                ownership.append({"sentence_id": sentence["id"], "selected_word_ids": [],
                                  "all_sentence_words_selected": False})
            ownership[-1]["selected_word_ids"].append(words[index]["id"])
            ownership[-1]["all_sentence_words_selected"] = (
                ownership[-1]["selected_word_ids"] == sentence["word_ids"])
        return {"source_words": [word["id"] for word in selected],
                "source_sentences": [row["sentence_id"] for row in ownership],
                "sentence_ownership": ownership,
                "source_sentences_meaning": "original_ownership_may_be_partial_not_full_sentence_selection",
                "text": "".join(word["text"] for word in selected),
                "text_policy": "verbatim_original_word_text_join_no_added_punctuation",
                "word_span": {"first_word_id": words[first]["id"], "last_word_id": words[last]["id"],
                              "start_seconds": min(word["start"] for word in selected),
                              "end_seconds": max(word["end"] for word in selected)},
                **_unapproved()}

    for event in events:
        _keys(event, EVENT_KEYS, "event")
        event_id = _identifier(event["id"], "event id")
        if event_id in event_ids:
            raise ValueError("duplicate event ID")
        event_ids.add(event_id)
        roles, relations = event["roles"], event["relations"]
        if not isinstance(roles, list) or not roles:
            raise ValueError("event roles must be a nonempty array")
        if not isinstance(relations, list):
            raise ValueError("event relations must be an array")
        role_count += len(roles)
        relation_count += len(relations)
        if role_count > MAX_ROLES or relation_count > MAX_RELATIONS:
            raise ValueError("outline exceeds fixed role/relation limits; no truncation")
        event_start, event_roles, local_roles = cursor, [], {}
        for position, role in enumerate(roles):
            _keys(role, ROLE_KEYS, "role")
            role_id = _identifier(role["id"], "role id")
            if role_id in role_ids:
                raise ValueError("role IDs must be globally unique")
            role_ids.add(role_id)
            if not isinstance(role["role"], str) or role["role"] not in ROLES:
                raise ValueError("unknown research role")
            first_id = _identifier(role["first_word_id"], "first_word_id")
            last_id = _identifier(role["last_word_id"], "last_word_id")
            if first_id not in indices or last_id not in indices:
                raise ValueError("role references an unknown original word ID")
            first, last = indices[first_id], indices[last_id]
            if first != cursor or first > last:
                raise ValueError("roles/events must cover original words once in order without gaps or overlap")
            local_roles[role_id] = position
            event_roles.append({"id": role_id, "role": role["role"], **describe(first, last)})
            cursor = last + 1
        event_relations, seen_relations = [], set()
        for relation in relations:
            _keys(relation, RELATION_KEYS, "relation")
            kind = relation["kind"]
            if not isinstance(kind, str) or kind not in RELATIONS:
                raise ValueError("unknown research relation")
            left = _identifier(relation["from_role"], "from_role")
            right = _identifier(relation["to_role"], "to_role")
            if left not in local_roles or right not in local_roles:
                raise ValueError("relation must reference roles in this same event")
            if local_roles[left] >= local_roles[right]:
                raise ValueError("relation must point from an earlier role to a later role")
            key = (kind, left, right)
            if key in seen_relations:
                raise ValueError("duplicate research relation")
            seen_relations.add(key)
            event_relations.append({**deepcopy(relation), **_unapproved()})
        result_events.append({"id": event_id, **describe(event_start, cursor - 1),
                              "roles": event_roles, "relations": event_relations})
    if cursor != len(words):
        raise ValueError("outline leaves original words uncovered")
    body = {"schema_version": "0.1.0-research.1", "artifact_kind": "event_outline",
            "stage": "semantic_draft_only", "strategy_version": VERSION,
            "producer": "Codex", "analysis_sha256": analysis_hash,
            "normalized_sha256": analysis["media"]["normalized_sha256"],
            "specification_sha256": content_hash(specification),
            "specification": deepcopy(specification), "time_reference": "normalized_video",
            "events": result_events,
            "structural_checks": {"all_original_words_covered_exactly_once": True,
                                  "source_word_order_preserved": True,
                                  "relations_only_forward_within_event": True},
            "verification_scope": "source_binding_and_structure_not_semantics_authorship_or_media_boundaries",
            "coverage_scope": "original_word_ownership_not_full_media_or_event_coverage",
            "limits": {"words": MAX_WORDS, "sentences": MAX_SENTENCES, "events": MAX_EVENTS,
                       "roles": MAX_ROLES, "relations": MAX_RELATIONS,
                       "json_bytes": MAX_JSON_BYTES, "json_nodes": MAX_JSON_NODES,
                       "json_depth": MAX_JSON_DEPTH, "text_characters": MAX_TEXT_CHARACTERS,
                       "id_characters": MAX_ID_CHARACTERS},
            "word_count": len(words), "role_count": role_count, "relation_count": relation_count,
            "frame_boundary_verified": False, "audible_word_alignment_verified": False,
            "words_changed": False, "human_labels_changed": False,
            **_unapproved()}
    body["outline_sha256"] = content_hash(body)
    # The derived evidence is larger than its inputs. Reject oversized output
    # here so every returned outline can also pass the verifier's JSON limits.
    _bounded_json(body, "outline")
    return body


def verify_event_outline(outline, analysis, specification):
    """Reconstruct every field; a recalculated digest is not approval."""
    _bounded_json(outline, "outline")
    expected = build_event_outline(analysis, specification)
    if outline != expected or content_hash(outline) != content_hash(expected):
        raise ValueError("event outline differs from bound source/specification reconstruction")
    return True
