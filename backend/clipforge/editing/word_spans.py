"""Offline, research-only word-span/frame proposals; never production candidates.

Inputs are original analysis plus a measured CFR source (or profile/source
manifest). This module does not measure media, edit ASR, render, call a model,
certify lexical boundaries, or approve an event's semantic completeness. Explicit
user listening evidence may retain an original-word-free event example, only
within its precisely bound scope; it cannot approve the whole event.
"""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
import re


VERSION = "word-span-frame-research-v1"
MAX_FRAMES = 2147483647


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN/Infinity is not valid evidence")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _seconds(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(label + " must be a finite nonnegative number, not bool")
    return Fraction(str(value))


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a nonempty ID")
    return value


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(label + " must be a SHA-256 hex digest")
    return value


def _floor(value):
    return value.numerator // value.denominator


def _ceil(value):
    return -(-value.numerator // value.denominator)


def _rational(value):
    return {"num": value.numerator, "den": value.denominator}


def _validated_inputs(analysis, measured_media):
    if not isinstance(analysis, dict) or not isinstance(measured_media, dict):
        raise ValueError("analysis and measured_media must be JSON objects")
    _finite(analysis)
    _finite(measured_media)
    if analysis.get("schema_version") != "0.1.0":
        raise ValueError("research input requires original analysis 0.1.0")
    media = analysis.get("media")
    if not isinstance(media, dict) or media.get("time_reference") != "normalized_video":
        raise ValueError("analysis must use normalized_video seconds")
    digest = _sha(media.get("normalized_sha256"), "normalized_sha256")
    duration = _seconds(media.get("duration_seconds"), "analysis duration")
    if duration <= 0 or type(media.get("has_audio")) is not bool:
        raise ValueError("analysis duration/audio declaration is invalid")
    source = measured_media.get("source", measured_media)
    if not isinstance(source, dict) or source.get("cfr") is not True:
        raise ValueError("requires a measured CFR source; average fps alone is insufficient")
    if _sha(source.get("sha256"), "measured source sha256") != digest:
        raise ValueError("analysis and measured normalized media identity differ")
    fps = source.get("fps")
    if (not isinstance(fps, dict) or set(fps) != {"num", "den"}
            or any(type(fps[k]) is not int or not 1 <= fps[k] <= MAX_FRAMES for k in fps)):
        raise ValueError("fps must contain positive integer num/den, not bool/float")
    rate = Fraction(fps["num"], fps["den"])
    if not 1 <= rate <= 120 or (rate.numerator, rate.denominator) != (fps["num"], fps["den"]):
        raise ValueError("fps must be a reduced rational between 1 and 120")
    frames = source.get("total_frames")
    if type(frames) is not int or not 1 <= frames <= MAX_FRAMES:
        raise ValueError("total_frames must be a positive integer, not bool")
    if "profile" in measured_media:
        profile = measured_media["profile"]
        if not isinstance(profile, dict) or profile.get("fps") != fps:
            raise ValueError("measured profile/source fps differ")
    frame_duration = Fraction(frames, 1) / rate
    # Matches the existing EDL media-description tolerance, expressed exactly.
    if abs(duration - frame_duration) > 1 / rate:
        raise ValueError("analysis duration and measured frame duration differ by more than one frame")
    if "audio_stream_index" in source:
        audio = source["audio_stream_index"]
        if (audio is not None and (type(audio) is not int or audio < 0)) or media["has_audio"] != (audio is not None):
            raise ValueError("analysis and measured media audio differ")
    words = analysis.get("words")
    sentences = analysis.get("sentences")
    if not isinstance(words, list) or not words or not isinstance(sentences, list):
        raise ValueError("requires nonempty original words and sentence ownership")
    ids, intervals, previous_start = set(), [], Fraction(-1)
    for word in words:
        if not isinstance(word, dict):
            raise ValueError("word must be an object")
        identifier = _identifier(word.get("id"), "word id")
        if identifier in ids:
            raise ValueError("duplicate word ID")
        ids.add(identifier)
        start, end = _seconds(word.get("start"), "word start"), _seconds(word.get("end"), "word end")
        if not 0 <= start < end <= duration or start < previous_start:
            raise ValueError("word ranges/order differ from the original timeline")
        if not isinstance(word.get("text"), str) or not word["text"]:
            raise ValueError("word text must be a nonempty original string")
        intervals.append((start, end))
        previous_start = start
    ownership, sentence_ids = [], set()
    by_id = {word["id"]: index for index, word in enumerate(words)}
    previous_sentence_start = Fraction(-1)
    for sentence in sentences:
        if not isinstance(sentence, dict):
            raise ValueError("sentence must be an object")
        identifier = _identifier(sentence.get("id"), "sentence id")
        refs = sentence.get("word_ids")
        if (identifier in sentence_ids or not isinstance(refs, list) or not refs
                or any(ref not in by_id for ref in refs)):
            raise ValueError("sentence ID/word ownership is invalid")
        sentence_ids.add(identifier)
        starts = [intervals[by_id[ref]][0] for ref in refs]
        ends = [intervals[by_id[ref]][1] for ref in refs]
        start = _seconds(sentence.get("start"), "sentence start")
        end = _seconds(sentence.get("end"), "sentence end")
        if start != min(starts) or end != max(ends) or start < previous_sentence_start:
            raise ValueError("original sentence ranges/order disagree with original words")
        previous_sentence_start = start
        ownership.extend(refs)
    if ownership != [word["id"] for word in words]:
        raise ValueError("sentences must own each original word exactly once in original order")
    return source, rate, frames, duration, words, intervals


def _coverage_risks(analysis, intervals, duration, lo, hi):
    """Report long ASR gaps only; VAD disagreement is never proven dialogue."""
    detectors = {}
    if "speech" in analysis:
        detectors["webrtc_vad"] = analysis["speech"]
    gate = analysis.get("analyzers", {}).get("asr", {}).get("parameters", {}).get("speech_gate")
    if isinstance(gate, dict) and "speech_intervals" in gate:
        detectors["silero_vad"] = gate["speech_intervals"]
    verified = {}
    for name, rows in detectors.items():
        if not isinstance(rows, list):
            raise ValueError("coverage detector intervals must be arrays")
        merged = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("coverage detector interval must be an object")
            a, b = _seconds(row.get("start"), "detector start"), _seconds(row.get("end"), "detector end")
            if not 0 <= a < b <= duration:
                raise ValueError("coverage detector range is invalid")
            if merged and a < merged[-1][0]:
                raise ValueError("coverage detector ranges must be sorted")
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        verified[name] = merged
    gaps, cursor = [], Fraction(0)
    for start, end in intervals:
        if start - cursor > 3:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor > 3:
        gaps.append((cursor, duration))
    risks = []
    for start, end in gaps:
        # Include a gap touching an endpoint, even if most lies outside a clip.
        if end < lo or start > hi:
            continue
        speech = {name: sum(max(Fraction(0), min(end, b) - max(start, a)) for a, b in rows)
                  for name, rows in verified.items()}
        risks.append({"start": float(start), "end": float(end),
                      "relation": "intersects_span" if start < hi and end > lo else "touches_span_boundary",
                      "detected_speech_seconds": {name: float(value) for name, value in speech.items()},
                      "suspect_detector_disagreement": any(value >= 3 for value in speech.values()),
                      "interpretation": "unverified_ASR_coverage_not_confirmed_missing_dialogue"})
    return {"status": "unverified", "available_detectors": sorted(verified),
            "long_gap_seconds_threshold": 3, "risks": risks,
            "coverage_verified": False}


def propose_word_span(analysis, measured_media, first_word_id, last_word_id):
    """Propose one contiguous original-word span with strictly safe frame edges.

    An edge is refused if no integer frame can contain every selected word
    without intersecting ANY excluded original word. No timestamp is edited.
    A safe ASR-token edge is not a certified lexical/event/speech boundary.
    """
    source, rate, frames, duration, words, intervals = _validated_inputs(analysis, measured_media)
    _identifier(first_word_id, "first_word_id")
    _identifier(last_word_id, "last_word_id")
    indices = {word["id"]: index for index, word in enumerate(words)}
    if first_word_id not in indices or last_word_id not in indices:
        raise ValueError("selected word ID does not exist")
    first, last = indices[first_word_id], indices[last_word_id]
    if first > last:
        raise ValueError("selected word IDs are reversed")
    selected = words[first:last + 1]
    selected_start = min(a for a, _ in intervals[first:last + 1])
    selected_end = max(b for _, b in intervals[first:last + 1])
    previous_end = max((b for _, b in intervals[:first]), default=Fraction(0))
    following_start = min((a for a, _ in intervals[last + 1:]), default=Fraction(frames, 1) / rate)
    if previous_end > selected_start or following_start < selected_end:
        raise ValueError("excluded overlapping original words cannot be split")
    start_min, start_max = _ceil(previous_end * rate), _floor(selected_start * rate)
    end_min, end_max = _ceil(selected_end * rate), _floor(following_start * rate)
    start, end = start_max, end_min
    if start_min > start_max or end_min > end_max:
        raise ValueError("no legal integer frame boundary in the original-word gap")
    if not 0 <= start < end <= frames:
        raise ValueError("selected word span exceeds measured video frame range")
    selected_ids = [word["id"] for word in selected]
    selected_set = set(selected_ids)
    owners = [{"sentence_id": row["id"],
               "selected_word_ids": [identifier for identifier in row["word_ids"] if identifier in selected_set],
               "all_sentence_words_selected": all(identifier in selected_set for identifier in row["word_ids"])}
              for row in analysis["sentences"] if any(identifier in selected_set for identifier in row["word_ids"])]
    source_in, source_out = Fraction(start, 1) / rate, Fraction(end, 1) / rate
    body = {"schema_version": "0.1.0-research.1", "artifact_kind": "word_span_proposal",
            "stage": "proposal_only", "strategy_version": VERSION,
            "analysis_sha256": content_hash(analysis), "normalized_sha256": source["sha256"],
            "measured_media_sha256": content_hash(measured_media),
            "measured_media_evidence": deepcopy(measured_media),
            "time_reference": "normalized_video", "source_words": selected_ids,
            "source_sentences": [row["sentence_id"] for row in owners],
            "source_sentences_meaning": "original_sentence_ownership_may_be_partial_not_full_sentence_selection",
            "sentence_ownership": owners, "text": "".join(word["text"] for word in selected),
            "text_policy": "verbatim_selected_original_word_text_join_no_added_punctuation",
            "word_span": {"first_word_id": first_word_id, "last_word_id": last_word_id,
                          "start_seconds": float(selected_start), "end_seconds": float(selected_end)},
            "frame_span": {"fps": deepcopy(source["fps"]), "total_frames": frames,
                           "source_in_frame": start, "source_out_frame": end,
                           "duration_frames": end - start, "start_seconds_exact": _rational(source_in),
                           "end_seconds_exact": _rational(source_out),
                           "duration_seconds": float(source_out - source_in),
                           "within_current_15_90_second_limit": 15 <= source_out - source_in <= 90},
            "boundary_evidence": {
                "start": {"previous_excluded_word_id": words[first - 1]["id"] if first else None,
                          "all_previous_words_max_end": float(previous_end),
                          "selected_words_min_start": float(selected_start),
                          "legal_frame_min": start_min, "legal_frame_max": start_max,
                          "chosen_frame": start},
                "end": {"next_excluded_word_id": words[last + 1]["id"] if last + 1 < len(words) else None,
                        "selected_words_max_end": float(selected_end),
                        "all_following_words_min_start": float(following_start),
                        "legal_frame_min": end_min, "legal_frame_max": end_max,
                        "chosen_frame": end}},
            "coverage_audit": _coverage_risks(analysis, intervals, duration, source_in, source_out),
            "engineering_checks": {"continuous_original_word_ids": True,
                                   "all_selected_words_contained_in_frame_span": True,
                                   "no_excluded_word_intersects_frame_span": True},
            "semantic_completeness_verified": False, "lexical_boundary_verified": False,
            "audible_word_alignment_verified": False, "automatically_recommendable": False,
            "production_candidate": False, "words_changed": False, "human_labels_changed": False}
    body["proposal_sha256"] = content_hash(body)
    return body


def verify_proposal(proposal, analysis, measured_media):
    """Reconstruct the full proposal; a correct digest alone is not validation."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("word_span"), dict):
        raise ValueError("proposal structure is invalid")
    expected = propose_word_span(analysis, measured_media,
                                 proposal["word_span"].get("first_word_id"),
                                 proposal["word_span"].get("last_word_id"))
    if proposal != expected:
        raise ValueError("proposal differs from bound original analysis/media evidence")
    return True


# Scoped listening evidence extends a research proposal without changing ASR.
REVIEWED_EXAMPLE_VERSION = "reviewed-non-dialogue-example-research-v1"
ANNOTATION_KEYS = {"schema_version", "artifact_kind", "analysis_sha256", "normalized_sha256",
                   "scope", "adjudication"}
ADJUDICATION_KEYS = {"reviewer", "method", "no_dialogue", "relation", "verbatim_user_statement"}


# Preserve the reviewed-example validator: unlike _seconds, it tests finiteness
# only for floats, matching the original standalone research helper exactly.
def _reviewed_seconds(value, label):
    if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)) or value < 0:
        raise ValueError(label + " must be a finite nonnegative number, not bool")
    return Fraction(str(value))








def _validate_annotation(annotation, proposal, analysis):
    if not isinstance(annotation, dict) or set(annotation) != ANNOTATION_KEYS:
        raise ValueError("annotation requires exactly the reviewed evidence fields")
    if (annotation["schema_version"] != "0.1.0-research.1"
            or annotation["artifact_kind"] != "reviewed_non_dialogue_example"):
        raise ValueError("unsupported reviewed-example annotation")
    if (annotation["analysis_sha256"] != proposal["analysis_sha256"]
            or annotation["analysis_sha256"] != content_hash(analysis)
            or annotation["normalized_sha256"] != proposal["normalized_sha256"]):
        raise ValueError("annotation identity differs from original analysis/normalized media")
    if analysis["media"]["has_audio"] is not True:
        raise ValueError("listening evidence requires a source with audio")
    scope = annotation["scope"]
    if not isinstance(scope, dict) or set(scope) != {"start_seconds", "end_seconds"}:
        raise ValueError("annotation scope requires exact original source seconds")
    start = _reviewed_seconds(scope["start_seconds"], "annotation start")
    end = _reviewed_seconds(scope["end_seconds"], "annotation end")
    if not 0 <= start < end <= _reviewed_seconds(analysis["media"]["duration_seconds"], "analysis duration"):
        raise ValueError("reviewed example scope exceeds original media")
    decision = annotation["adjudication"]
    if (not isinstance(decision, dict) or set(decision) != ADJUDICATION_KEYS
            or decision["reviewer"] != "user" or decision["method"] != "listening"
            or decision["no_dialogue"] is not True or decision["relation"] != "same_event_example"
            or not isinstance(decision["verbatim_user_statement"], str)
            or not decision["verbatim_user_statement"].strip()):
        raise ValueError("requires explicit user listening: no dialogue and same-event example")
    return start, end


def extend_with_reviewed_example(proposal, analysis, measured_media, annotation):
    """Extend only the reviewed trailing music scope, stopping before next word.

    The annotation must run exactly from the last selected original word's max
    end to the next original word's start. End uses floor(next_start * fps), so
    an incomplete final video frame is reported as quantization loss rather
    than including the beginning of the next spoken word.
    """
    verify_proposal(proposal, analysis, measured_media)
    reviewed_start, reviewed_end = _validate_annotation(annotation, proposal, analysis)
    selected_ids = proposal["source_words"]
    words = analysis["words"]
    last_index = next(index for index, word in enumerate(words) if word["id"] == selected_ids[-1])
    if last_index + 1 >= len(words):
        raise ValueError("requires an existing next original word as the reviewed example endpoint")
    selected = [word for word in words if word["id"] in set(selected_ids)]
    selected_end = max(_reviewed_seconds(word["end"], "selected word end") for word in selected)
    next_word = words[last_index + 1]
    next_start = _reviewed_seconds(next_word["start"], "next original word start")
    if reviewed_start != selected_end or reviewed_end != next_start:
        raise ValueError("annotation must cover exactly the trailing original-word-free example gap")
    # The base verifier already checks overlap at its original endpoints. This
    # additional check proves that extending through this scope introduces no
    # original excluded word, including an older long overlapping word.
    selected_set = set(selected_ids)
    for word in words:
        if word["id"] not in selected_set:
            start, end = _reviewed_seconds(word["start"], "excluded word start"), _reviewed_seconds(word["end"], "excluded word end")
            if start < reviewed_end and end > reviewed_start:
                raise ValueError("reviewed example contains an excluded original word")
    fps = proposal["frame_span"]["fps"]
    rate = Fraction(fps["num"], fps["den"])
    first_frame = proposal["frame_span"]["source_in_frame"]
    old_out = proposal["frame_span"]["source_out_frame"]
    new_out = _floor(reviewed_end * rate)
    if not old_out < new_out <= proposal["frame_span"]["total_frames"]:
        raise ValueError("reviewed scope has no additional safely includable video frames")
    actual_end = Fraction(new_out, 1) / rate
    if actual_end > reviewed_end or actual_end < selected_end:
        raise ValueError("reviewed extension would intersect the next original word or clip selected words")
    retained_seconds = actual_end - reviewed_start
    omitted_tail = reviewed_end - actual_end
    annotation_sha = content_hash(annotation)
    result = deepcopy(proposal)
    result.pop("proposal_sha256")
    result["strategy_version"] = REVIEWED_EXAMPLE_VERSION
    result["base_proposal_sha256"] = proposal["proposal_sha256"]
    result["base_proposal"] = deepcopy(proposal)
    result["annotation_sha256"] = annotation_sha
    result["reviewed_annotation"] = deepcopy(annotation)
    frame_span = result["frame_span"]
    frame_span.update({"source_out_frame": new_out, "duration_frames": new_out - first_frame,
                       "end_seconds_exact": _rational(actual_end),
                       "duration_seconds": float(Fraction(new_out - first_frame, 1) / rate),
                       "within_current_15_90_second_limit": 15 <= Fraction(new_out - first_frame, 1) / rate <= 90})
    result["included_non_dialogue_spans"] = [{
        "annotation_sha256": annotation_sha, "classification": "user_reviewed_music_no_dialogue",
        "event_relation": "user_reviewed_same_event_example",
        "annotated_start_seconds": float(reviewed_start), "annotated_end_seconds": float(reviewed_end),
        "retained_start_seconds_exact": _rational(reviewed_start),
        "retained_end_seconds_exact": _rational(actual_end),
        "retained_duration_seconds_exact": _rational(retained_seconds),
        "fully_inside_example_in_frame": _ceil(reviewed_start * rate),
        "fully_inside_example_out_frame": new_out,
        "first_frame_may_also_contain_selected_word_tail": reviewed_start * rate != _floor(reviewed_start * rate),
        "omitted_tail_seconds_exact": _rational(omitted_tail),
        "omitted_tail_seconds": float(omitted_tail),
        "quantization_policy": "floor_next_original_word_start_no_next_word_audio_or_frame_claim",
        "full_annotated_scope_retained": omitted_tail == 0,
        "lyrics_inferred": False, "transcript_words_added": False}]
    result["boundary_evidence"]["end"] = {
        "kind": "explicit_user_reviewed_non_dialogue_example",
        "annotation_sha256": annotation_sha, "next_excluded_word_id": next_word["id"],
        "next_excluded_word_start_seconds": float(next_start),
        "chosen_frame": new_out, "rounding": "floor", "chosen_seconds_exact": _rational(actual_end),
        "previous_original_word_end_boundary_preserved_in_base_proposal": True}
    # Keep this field byte-for-byte equivalent as JSON data to the base report;
    # the user adjudication is an additional scoped evidence layer, not a new
    # detector output or a global declaration that ASR coverage is complete.
    result["human_adjudication"] = {
        "annotation_sha256": annotation_sha, "scope": deepcopy(annotation["scope"]),
        "no_dialogue": True, "same_event_example": True,
        "raw_coverage_audit_unchanged": True, "other_coverage_scopes_adjudicated": False,
        "semantic_completeness_verified": False}
    result["engineering_checks"].update({"source_words_and_text_unchanged": True,
                                         "next_original_word_not_intersected": True,
                                         "non_dialogue_example_kept_with_event": True})
    result["proposal_sha256"] = content_hash(result)
    return result


def verify_reviewed_example(proposal, analysis, measured_media, annotation):
    """Verify full reconstruction, including user annotation and quantization."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("base_proposal"), dict):
        raise ValueError("reviewed example proposal must retain its original base proposal")
    expected = extend_with_reviewed_example(proposal["base_proposal"], analysis, measured_media, annotation)
    if proposal != expected:
        raise ValueError("reviewed example proposal differs from bound original evidence")
    return True
