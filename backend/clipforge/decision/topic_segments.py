"""Find lexical topic valleys before generating whole-topic candidates.

This is a deterministic, text-only baseline, not a claim of semantic event
completeness. No scene-specific words, model calls, or sentence-window fallback
are used. Uninformative windows have no similarity measurement; they are never
treated as a zero-similarity topic change.
"""

from bisect import bisect_left
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math

from clipforge.analysis.validation import validate_analysis
from clipforge.analysis.sentences import END_MARK, SENTENCE_PARAMETERS, split_sentences_with_trace
from clipforge.decision.candidates import ROOT, WindowOptions, _units, content_hash
from clipforge.decision.topics import features


GENERATOR_VERSION = "topic-valley-v1"
GUARDED_GENERATOR_VERSION = "topic-valley-v2"
EVENT_GENERATOR_VERSION = "topic-valley-v3"
CONTINUITY_GENERATOR_VERSION = "topic-valley-v4"
TOPIC_VERSIONS = (GENERATOR_VERSION, GUARDED_GENERATOR_VERSION, EVENT_GENERATOR_VERSION,
                  CONTINUITY_GENERATOR_VERSION)
# Fixed resource ceilings, not corpus-fitted decision thresholds. Input is
# rejected explicitly instead of truncated. Feature construction is linear in
# text length; each boundary examines at most 2 * MAX_WINDOW_UNITS units.
MAX_SENTENCES = 10000
MAX_TEXT_CHARACTERS = 1000000
MAX_ANALYSIS_BYTES = 16 * 1024 * 1024
MAX_WINDOW_UNITS = 32


@dataclass(frozen=True)
class TopicOptions(WindowOptions):
    window_units: int = 3
    min_segment_units: int = 2
    min_depth: float = 0.15

    def __post_init__(self):
        super().__post_init__()
        if (type(self.window_units) is not int
                or not 1 <= self.window_units <= MAX_WINDOW_UNITS):
            raise ValueError(f"window_units 必须为 1..{MAX_WINDOW_UNITS} 的整数")
        if (type(self.min_segment_units) is not int
                or not 1 <= self.min_segment_units <= MAX_SENTENCES):
            raise ValueError(f"min_segment_units 必须为 1..{MAX_SENTENCES} 的整数")
        if (isinstance(self.min_depth, bool)
                or not isinstance(self.min_depth, (int, float))
                or not math.isfinite(self.min_depth)
                or not 0 < self.min_depth <= 1):
            raise ValueError("min_depth 必须为 (0, 1] 内的有限数值")


def _validated_input(analysis, options, analysis_schema):
    if not isinstance(options, TopicOptions):
        raise ValueError("话题生成需要 TopicOptions 参数")
    if not isinstance(analysis, dict):
        raise ValueError("analysis 必须为对象")
    sentences = analysis.get("sentences")
    if not isinstance(sentences, list):
        raise ValueError("analysis.sentences 必须为数组")
    if len(sentences) > MAX_SENTENCES:
        raise ValueError(f"句子数量超过固定上限 {MAX_SENTENCES}；未截断输入")
    characters = 0
    for sentence in sentences:
        if not isinstance(sentence, dict) or not isinstance(sentence.get("text"), str):
            raise ValueError("句子必须包含字符串 text")
        characters += len(sentence["text"])
        if characters > MAX_TEXT_CHARACTERS:
            raise ValueError(f"句子文字超过固定上限 {MAX_TEXT_CHARACTERS}；未截断输入")
    # Stop as soon as the serialized size exceeds the ceiling, before analysis
    # validation, feature extraction or allocating a complete serialized copy.
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), allow_nan=False)
    size = 0
    for chunk in encoder.iterencode(analysis):
        size += len(chunk.encode("utf-8"))
        if size > MAX_ANALYSIS_BYTES:
            raise ValueError(f"分析数据超过固定上限 {MAX_ANALYSIS_BYTES} 字节；未截断输入")
    schema = analysis_schema or json.loads(
        (ROOT / "schemas/analysis.schema.json").read_text(encoding="utf-8"))
    validate_analysis(analysis, schema)


def _window_counts(counts, first, last):
    result = Counter()
    for index in range(first, last):
        result.update(counts[index])
    return result


def _similarity(left, right, idf):
    if not left or not right:
        return None
    a = {term: (1 + math.log(count)) * idf[term]
         for term, count in left.items()}
    b = {term: (1 + math.log(count)) * idf[term]
         for term, count in right.items()}
    norm_a = math.sqrt(math.fsum(value * value for value in a.values()))
    norm_b = math.sqrt(math.fsum(value * value for value in b.values()))
    if len(a) > len(b):
        a, b = b, a
    dot = math.fsum(value * b.get(term, 0.0) for term, value in a.items())
    # Fixed serialization precision also makes genuinely flat valley plateaus
    # independent of irrelevant floating-point summation noise.
    return round(max(0.0, min(1.0, dot / (norm_a * norm_b))), 12)


def _valleys(boundaries, first, last):
    """Annotate valleys in one uninterrupted, informative similarity curve.

    Scores at both sides must rise above the plateau. Each peak is the first
    local maximum reached moving away from that plateau. Relative prominence
    divides the combined drops by the combined peaks, avoiding dependence on
    the curve's absolute lexical-overlap scale. The raw drops remain evidence.
    Flat or unbracketed curves provide no evidence for a lexical split.
    """
    proposals = []
    cursor = first
    while cursor < last:
        value = boundaries[cursor]["similarity"]
        stop = cursor + 1
        while stop < last and boundaries[stop]["similarity"] == value:
            stop += 1
        if (cursor > first and stop < last
                and boundaries[cursor - 1]["similarity"] > value
                and boundaries[stop]["similarity"] > value):
            left = cursor - 1
            while left > first and (boundaries[left - 1]["similarity"]
                                    >= boundaries[left]["similarity"]):
                left -= 1
            right = stop
            while right + 1 < last and (boundaries[right + 1]["similarity"]
                                       >= boundaries[right]["similarity"]):
                right += 1
            left_peak = boundaries[left]["similarity"]
            right_peak = boundaries[right]["similarity"]
            raw_depth = (left_peak - value) + (right_peak - value)
            # Strictly greater bracketing peaks imply a positive denominator;
            # a flat zero curve cannot enter this branch or invent a valley.
            depth = round(raw_depth / (left_peak + right_peak), 12)
            plateau = [boundaries[i]["after_unit"] for i in range(cursor, stop)]
            representative = cursor + (stop - cursor - 1) // 2
            for index in range(cursor, stop):
                boundary = boundaries[index]
                boundary.update(left_peak=left_peak, right_peak=right_peak,
                                raw_depth=round(raw_depth, 12), depth=depth)
                boundary["reasons"] = ["plateau_alternative"]
            proposals.append(representative)
            # Repeating a plateau's full index list at every alternative would
            # make the saved audit report quadratic for long flat valleys.
            boundaries[representative]["plateau_after_units"] = plateau
            boundaries[representative]["reasons"] = []
        cursor = stop
    return proposals


def _event_cut_evidence(units, boundaries, hard_splits, *, continuity=False):
    """Index explicit dependencies in linear space, before proposing cuts.

    Long quotation spans are saved once. A sweep stores only their active count
    at each boundary, rather than duplicating the complete evidence at every
    cut. Dependencies across hard media pauses cannot be joined; all affected
    units stay in the report but cannot be recommended as complete fragments.
    """
    if continuity:
        from clipforge.decision.event_continuity import build_event_continuity
        report = build_event_continuity(units)
    else:
        from clipforge.decision.event_dependencies import build_event_dependencies
        report = build_event_dependencies(units)
    count = len(units)
    active_delta, unresolved_delta = [0] * (count + 1), [0] * (count + 1)

    def unresolved(first, last):
        unresolved_delta[first] += 1
        unresolved_delta[last + 1] -= 1

    for link in report['links']:
        first, last = link['first_unit'], link['last_unit']
        active_delta[first] += 1
        active_delta[last] -= 1
        crosses_hard_pause = (bisect_left(hard_splits, first + 1)
                              < bisect_left(hard_splits, last + 1))
        link['retention'] = ('interrupted_by_hard_pause' if crosses_hard_pause
                             else 'must_remain_in_one_segment')
        if crosses_hard_pause:
            unresolved(first, last)
    for edge in report['open_edges']:
        # A standalone 'this one' at the end can be a complete reference. Keep
        # that ambiguity visible instead of turning it into a fabricated fail.
        if edge['kind'] == 'unresolved_demonstrative':
            edge['retention'] = 'warning_only_ambiguous_reference'
            continue
        first = 0 if edge['kind'] == 'quotation_missing_opener' else edge['unit']
        last = count - 1 if edge['kind'] == 'quotation_missing_closer' else edge['unit']
        edge['retention'] = 'unresolved_source_edge'
        unresolved(first, last)
    active, missing, unresolved_prefix = 0, 0, [0]
    for index in range(count):
        active += active_delta[index]
        missing += unresolved_delta[index]
        unresolved_prefix.append(unresolved_prefix[-1] + bool(missing))
        if index < len(boundaries):
            boundaries[index]['event_support'] = {
                'active_dependencies': active,
                'cut_allowed': active == 0,
                'semantic_completeness_verified': False,
            }
    return report, unresolved_prefix


def segment_topics(analysis, options=None, *, analysis_schema=None,
                   version=GENERATOR_VERSION):
    """Return complete segmentation plus accepted and rejected gap evidence.

    Indices and inclusive window ranges are zero-based. Hard pauses are applied
    first using the same strict ``gap > max_gap_seconds`` contract as the legacy
    generator. Lexical boundaries are ranked by depth, then similarity, then
    source order; each must leave at least ``min_segment_units`` on both sides
    of its nearest accepted boundaries. Hard pauses may produce shorter units.
    """
    options = options or TopicOptions()
    if version not in TOPIC_VERSIONS:
        raise ValueError("unknown topic generator version")
    _validated_input(analysis, options, analysis_schema)
    guarded = version in (GUARDED_GENERATOR_VERSION, EVENT_GENERATOR_VERSION,
                          CONTINUITY_GENERATOR_VERSION)
    event_aware = version in (EVENT_GENERATOR_VERSION, CONTINUITY_GENERATOR_VERSION)
    coverage = None
    blocking_coverage = []
    unverified_compound_ends = set()
    if guarded:
        from clipforge.analysis.coverage import audit_word_coverage
        coverage = audit_word_coverage(analysis)
        for gap in coverage["suspect_intervals"]:
            detectors = gap["detectors"]
            # Whisper's speech gate is Silero. A lone WebRTC/music disagreement
            # remains visible, but is not treated as established missed speech.
            detector = "silero_vad" if "silero_vad" in detectors else "webrtc_vad"
            if detectors.get(detector, {}).get("speech_seconds", 0) >= 3.0:
                blocking_coverage.append(gap)
        # A short final syllable cannot meet the long-gap audit threshold.
        # If bounded ASR recovery still cannot cover that measured overhang,
        # do not silently recommend the same truncated final utterance again.
        recovery = analysis['analyzers']['asr']['parameters'].get('coverage_recovery') or {}
        if recovery.get('version') == 'local-word-coverage-recovery-v2':
            for gap in recovery.get('remaining_recovery_gaps', []):
                if gap.get('reason') == 'terminal_speech_beyond_last_word':
                    start, end = gap.get('start'), gap.get('end')
                    if (type(start) not in (int, float) or type(end) not in (int, float)
                            or not 0 <= start < end <= analysis['media']['duration_seconds']):
                        raise ValueError('invalid unresolved terminal ASR interval')
                    blocking_coverage.append(deepcopy(gap))
        if analysis['analyzers']['asr']['parameters'].get('sentence_rules') == SENTENCE_PARAMETERS:
            replayed, trace = split_sentences_with_trace(analysis['words'])
            if replayed != analysis['sentences']:
                raise ValueError('current sentence rules cannot reproduce the guarded analysis')
            unverified_compound_ends = {row['sentence_id'] for row in trace
                                       if row['reason'] == 'unverified_compound_gap'}
    identity = content_hash(analysis)
    option_data = asdict(options)
    units = _units(analysis["sentences"])
    counts = [features("\n".join(s["text"] for s in unit["sentences"]))
              for unit in units]
    df = Counter(term for count in counts for term in count)
    idf = {term: 1 + math.log((1 + len(units)) / (1 + frequency))
           for term, frequency in df.items()}
    boundaries = []
    hard_splits = []
    for index in range(max(0, len(units) - 1)):
        gap = units[index + 1]["start"] - units[index]["end"]
        hard = gap > options.max_gap_seconds
        if hard:
            hard_splits.append(index + 1)
        boundaries.append({
            "after_unit": index, "gap_seconds": gap,
            "left_sentences": [s["id"] for s in units[index]["sentences"]],
            "right_sentences": [s["id"] for s in units[index + 1]["sentences"]],
            "left_window_units": None, "right_window_units": None,
            "similarity": None, "left_peak": None, "right_peak": None,
            "raw_depth": None, "depth": None, "plateau_after_units": [],
            "decision": "hard_pause" if hard else "not_valley",
            "reasons": ["gap_above_max_gap"] if hard else ["not_local_minimum_or_unbracketed"],
        })
        if guarded:
            # A length-based ASR chunk end supplies no utterance-end evidence.
            # Use the existing ASR pause definition, not footage-specific words.
            edge_sentences = [s for s in units[index]["sentences"]
                              if s["end"] == units[index]["end"]]
            terminal = bool(edge_sentences) and all(
                END_MARK.search(s["text"].rstrip()) for s in edge_sentences)
            pause = gap + 1e-9 >= SENTENCE_PARAMETERS["pause_seconds"]
            boundaries[-1]["cut_support"] = {
                "terminal_punctuation": terminal,
                "pause_seconds_required": SENTENCE_PARAMETERS["pause_seconds"],
                "pause_observed": pause,
                "supported": terminal or pause,
                "semantic_completeness_verified": False,
                "compound_across_asr_gap": any(s['id'] in unverified_compound_ends
                                              for s in edge_sentences),
            }

    event_report, unresolved_prefix = (None, None)
    if event_aware:
        event_report, unresolved_prefix = _event_cut_evidence(
            units, boundaries, hard_splits,
            continuity=version == CONTINUITY_GENERATOR_VERSION)

    accepted = set(hard_splits)
    edges = [0, *hard_splits, len(units)]
    for block_first, block_last in zip(edges, edges[1:]):
        # Window contexts cannot cross previously fixed long-pause boundaries.
        for index in range(block_first, block_last - 1):
            first = max(block_first, index + 1 - options.window_units)
            last = min(block_last, index + 1 + options.window_units)
            boundary = boundaries[index]
            boundary["left_window_units"] = [first, index]
            boundary["right_window_units"] = [index + 1, last - 1]
            boundary["similarity"] = _similarity(
                _window_counts(counts, first, index + 1),
                _window_counts(counts, index + 1, last), idf)
            if boundary["similarity"] is None:
                boundary["reasons"] = ["uninformative_window"]
        # Missing measurements break the curve. An empty window must never
        # create an artificial valley or allow a peak across missing evidence.
        proposals = []
        cursor = block_first
        while cursor < block_last - 1:
            if boundaries[cursor]["similarity"] is None:
                cursor += 1
                continue
            stop = cursor + 1
            while stop < block_last - 1 and boundaries[stop]["similarity"] is not None:
                stop += 1
            proposals.extend(_valleys(boundaries, cursor, stop))
            cursor = stop
        eligible = []
        for index in proposals:
            boundary = boundaries[index]
            if guarded and not boundary["cut_support"]["supported"]:
                boundary["decision"] = "rejected"
                boundary["reasons"] = ["no_utterance_end_evidence"]
            elif boundary["depth"] < options.min_depth:
                boundary["decision"] = "rejected"
                boundary["reasons"] = ["below_min_depth"]
            else:
                eligible.append(index)
        nearest = [block_first, block_last]
        for index in sorted(eligible, key=lambda i: (
                -boundaries[i]["depth"], boundaries[i]["similarity"], i)):
            split = index + 1
            position = bisect_left(nearest, split)
            left_distance = split - nearest[position - 1]
            right_distance = nearest[position] - split
            boundary = boundaries[index]
            if min(left_distance, right_distance) < options.min_segment_units:
                boundary["decision"] = "rejected"
                boundary["reasons"] = ["min_segment_units_conflict"]
            else:
                nearest.insert(position, split)
                accepted.add(split)
                boundary["decision"] = "accepted_valley"
                boundary["reasons"] = []

    if event_aware:
        # Freeze the v2 greedy selection first. Rejecting an event cut earlier
        # could release a conflicting nearby valley and introduce a different
        # cut in the answer. V3 may only remove existing cuts, never reschedule.
        for boundary in boundaries:
            if (boundary['decision'] == 'accepted_valley'
                    and not boundary['event_support']['cut_allowed']):
                accepted.remove(boundary['after_unit'] + 1)
                boundary['decision'] = 'rejected'
                boundary['reasons'] = ['event_dependency_crosses_cut']

    segments = []
    cuts = [0, *sorted(accepted), len(units)] if units else []
    for first, last in zip(cuts, cuts[1:]):
        members = [s for unit in units[first:last] for s in unit["sentences"]]
        references = [s["id"] for s in members]
        duration = round(units[last - 1]["end"] - units[first]["start"], 9)
        reasons = []
        if duration < options.min_seconds:
            reasons.append("below_min_seconds")
        if duration > options.max_seconds:
            reasons.append("above_max_seconds")
        if guarded:
            # Include adjoining endpoints: a no-word speech gap also makes the
            # clips immediately before/after it unsafe to recommend unchanged.
            if any(units[first]["start"] <= gap["end"]
                   and units[last - 1]["end"] >= gap["start"]
                   for gap in blocking_coverage):
                reasons.append("suspect_asr_coverage")
            if ((first > 0 and boundaries[first - 1]['cut_support']['compound_across_asr_gap'])
                    or (last < len(units) and boundaries[last - 1]['cut_support']['compound_across_asr_gap'])):
                reasons.append('unverified_compound_gap')
        if event_aware and unresolved_prefix[last] > unresolved_prefix[first]:
            reasons.append('unresolved_event_dependency')
        key = {"analysis": identity, "version": version,
               "options": option_data, "source_sentences": references}
        segments.append({
            "id": "t_" + content_hash(key), "first_unit": first, "last_unit": last - 1,
            "start": units[first]["start"], "end": units[last - 1]["end"],
            "duration_seconds": duration, "source_sentences": references,
            "text": "\n".join(s["text"] for s in members),
            "eligible": not reasons, "reasons": reasons,
        })
    warnings = []
    if not units:
        warnings.append("no_sentences")
    if any(not count for count in counts):
        warnings.append("uninformative_sentence_unit")
    if any(b["reasons"] == ["uninformative_window"] for b in boundaries):
        warnings.append("uninformative_window")
    if any("below_min_seconds" in s["reasons"] for s in segments):
        warnings.append("short_topic_segment_not_generated")
    if any("above_max_seconds" in s["reasons"] for s in segments):
        warnings.append("long_topic_segment_not_generated")
    if not any(s["eligible"] for s in segments):
        warnings.append("no_eligible_topic_segment")
    if guarded and any("no_utterance_end_evidence" in b["reasons"] for b in boundaries):
        warnings.append("unsupported_topic_cut_rejected")
    if guarded and coverage["suspect_intervals"]:
        warnings.append("suspect_asr_coverage")
    if guarded and len(blocking_coverage) < len(coverage["suspect_intervals"]):
        warnings.append("coverage_detector_disagreement")
    if guarded and any('unverified_compound_gap' in s['reasons'] for s in segments):
        warnings.append('unverified_compound_gap')
    if event_aware:
        if any('event_dependency_crosses_cut' in b['reasons'] for b in boundaries):
            warnings.append('event_cut_rejected')
        if any('unresolved_event_dependency' in s['reasons'] for s in segments):
            warnings.append('unresolved_event_dependency')
        if (any(e['retention'] == 'warning_only_ambiguous_reference'
                for e in event_report['open_edges'])
                or event_report.get('ambiguities')):
            warnings.append('ambiguous_event_reference')
    result = {
        "schema_version": "0.1.0-draft.1", "artifact_kind": "topic_segmentation",
        "version": version, "analysis_sha256": identity,
        "options": option_data,
        "method": "adjacent-window TF-IDF cosine valleys; relative peak prominence; Chinese 2/3-grams",
        "scope": "lexical topic boundary evidence; not semantic event-completeness verification",
        "limits": {"max_sentences": MAX_SENTENCES,
                   "max_text_characters": MAX_TEXT_CHARACTERS,
                   "max_analysis_bytes": MAX_ANALYSIS_BYTES,
                   "max_window_units": MAX_WINDOW_UNITS},
        "units": [{"index": index, "start": unit["start"], "end": unit["end"],
                   "source_sentences": [s["id"] for s in unit["sentences"]]}
                  for index, unit in enumerate(units)],
        "boundaries": boundaries, "segments": segments,
        "warnings": warnings, "model_requests": 0,
    }
    if guarded:
        result["word_coverage"] = coverage
        result["blocking_coverage_intervals"] = blocking_coverage
        result["cut_policy"] = {
            "version": "utterance-evidence-v1",
            "requirement": "terminal punctuation or existing ASR pause threshold",
            "pause_seconds": SENTENCE_PARAMETERS["pause_seconds"],
            "semantic_completeness_verified": False,
            "unsupported_cuts": "reject valley; retain whole segment, never pad or force a shorter cut",
            "suspect_coverage": "exclude adjoining/intersecting Silero-supported gaps; WebRTC fallback only when Silero is absent",
            "single_detector_disagreement": "retain warning; not proof of missing speech",
        }
    if event_aware:
        result['event_dependencies'] = event_report
        result['event_policy'] = {
            'version': 'surface-dependency-cuts-v1',
            'semantic_completeness_verified': False,
            'dependency_cuts': 'reject lexical cuts through explicit surface dependencies before generating candidates',
            'baseline_cuts': 'freeze v2 greedy selection; only remove cuts, never reschedule rejected alternatives',
            'hard_pauses': 'never bridge; retain and exclude fragments with interrupted dependencies',
            'source_edges': 'exclude explicit missing continuation or quotation; ambiguous demonstratives warn only',
            'duration_limits': 'retain whole linked segment; report short/long exclusions without truncation or fallback',
        }
        if version == CONTINUITY_GENERATOR_VERSION:
            result['event_policy'].update(
                version='paired-discourse-cuts-v2',
                paired_evidence='implicit requests and causal relations require matching evidence on both sides',
                answer_continuations='retain linked pending explanations beyond the first response unit',
                closure_scope='no detected open slot is not semantic event-completeness verification')
    return result


def generate_topic_candidates(analysis, options=None, *, analysis_schema=None,
                              version=GENERATOR_VERSION):
    """Generate only full eligible segments; never trim or join rejected ones."""
    options = options or TopicOptions()
    report = segment_topics(analysis, options, analysis_schema=analysis_schema, version=version)
    eligible = [s for s in report["segments"] if s["eligible"]]
    if len(eligible) > options.max_candidates:
        raise ValueError("话题候选数量超过 max_candidates；未截断或回退到句子滑窗")
    candidates = []
    for segment in eligible:
        key = {"analysis": report["analysis_sha256"], "version": version,
               "options": asdict(options), "source_sentences": segment["source_sentences"]}
        candidates.append({
            "id": "c_" + content_hash(key), "start": segment["start"],
            "end": segment["end"], "duration_seconds": segment["duration_seconds"],
            "text": segment["text"], "source_sentences": segment["source_sentences"],
            "scoring_status": "pending", "score": None, "reasons": [], "scorer": None,
        })
    warnings = []
    if "no_sentences" in report["warnings"]:
        warnings.append("no_sentences")
    if "short_topic_segment_not_generated" in report["warnings"]:
        warnings.append("topic_short_segment_skipped")
    if "long_topic_segment_not_generated" in report["warnings"]:
        warnings.append("topic_long_segment_skipped")
    if any(w.startswith("uninformative_") for w in report["warnings"]):
        warnings.append("topic_uninformative_text")
    if "unsupported_topic_cut_rejected" in report["warnings"]:
        warnings.append("topic_unsupported_cut_rejected")
    if "suspect_asr_coverage" in report["warnings"]:
        warnings.append("topic_suspect_asr_coverage")
    if "coverage_detector_disagreement" in report["warnings"]:
        warnings.append("topic_coverage_detector_disagreement")
    if 'unverified_compound_gap' in report['warnings']:
        warnings.append('topic_unverified_compound_gap')
    if 'event_cut_rejected' in report['warnings']:
        warnings.append('topic_event_cut_rejected')
    if 'unresolved_event_dependency' in report['warnings']:
        warnings.append('topic_event_dependency_unresolved')
    if 'ambiguous_event_reference' in report['warnings']:
        warnings.append('topic_event_dependency_warning')
    if not candidates:
        warnings.append("no_eligible_window")
    return {
        "schema_version": "0.1.0-draft.1", "artifact_kind": "candidate_windows",
        "stage": "generated", "input_result_kind": analysis["result_kind"],
        "analysis_sha256": report["analysis_sha256"], "media": deepcopy(analysis["media"]),
        "generator": {"version": version, "options": asdict(options)},
        "candidate_count": len(candidates), "candidates": candidates, "warnings": warnings,
    }
