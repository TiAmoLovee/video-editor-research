"""Derive immutable subtitle evidence from saved EDL cuts and complete analysis.

No ASR correction or model call occurs. Word timestamps are saved evidence, not
a claim of measured agreement with real speech. All cue ranges are half-open.
"""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import unicodedata

from clipforge.analysis.validation import validate_analysis
from clipforge.editing.edl import _schema_validate, frame_rate, validate_edl

ROOT = Path(__file__).resolve().parents[3]
POLICY = {
    "max_lines": 2, "target_cjk_characters_per_line": 18, "max_cue_seconds": 4,
    "pause_seconds": 0.45, "duration_tolerance_frames": 1,
    "frame_quantization": "nearest_half_up_clip_clamped_min_one_frame",
    "serialization_quantization": "ceil_start_floor_end",
    "overlap_policy": "merge_overlapping_groups_report_exceptions",
    "ass_literal_policy": "fullwidth_ascii_braces_backslash",
    "srt_literal_policy": "fullwidth_ascii_angle_brackets",
    "orphan_tail_max_cjk_characters": 2, "orphan_tail_max_seconds": 0.3,
    "orphan_merge_max_seconds": 4.5,
    "orphan_tail_policy": "same_sentence_same_pause_merge_or_rebalance_without_retiming",
    "text_policy": "original_words_layout_only",
}
PORTRAIT_POLICY = {**POLICY, "target_cjk_characters_per_line": 12,
                   "layout": "portrait_9_16", "font_sizing": "width_height_and_cue_constrained"}
BRAND = {"text": "AI辅助剪辑", "position": "top_right", "scope": "whole_timeline"}
INTEGER_KEYS = {"num", "den", "width", "height", "duration_frames", "source_in_frame", "source_out_frame",
                "timeline_in_frame", "timeline_out_frame", "max_lines", "target_cjk_characters_per_line",
                "max_cue_seconds", "duration_tolerance_frames", "orphan_tail_max_cjk_characters"}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _schema(name):
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def _strict_integers(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in INTEGER_KEYS and type(item) is not int:
                raise ValueError(f"{key} requires a JSON integer")
            _strict_integers(item)
    elif isinstance(value, list):
        for item in value:
            _strict_integers(item)


def _text(value):
    if any((ord(char) < 32 and char != "\n") or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError("subtitle text contains unsupported display control characters")
    return value


def _columns(value):
    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
               for char in value)


def _layout(words, policy):
    raw = "".join(_text(word["text"]) for word in words)
    display = raw.replace("\n", " ")  # Reflow only; raw word text remains in evidence.
    flags = ["source_linebreak_reflowed"] if raw != display else []
    total = _columns(display)
    line_columns = 2 * policy["target_cjk_characters_per_line"]
    if total <= line_columns:
        return display, flags
    if total > line_columns * policy["max_lines"]:
        flags.append("line_width_exception")
    boundaries, cursor = [], 0
    for word in words[:-1]:
        cursor += len(word["text"])
        if _columns(display[:cursor]) <= line_columns and _columns(display[cursor:]) <= line_columns:
            boundaries.append(cursor)
    if boundaries:
        split = min(boundaries, key=lambda point: abs(_columns(display[:point]) - total / 2))
    else:
        target, width, split = min(line_columns, total / 2) if total <= 2 * line_columns else total / 2, 0, 1
        for index, char in enumerate(display[:-1], 1):
            width += _columns(char)
            split = index
            if width >= target:
                break
        flags.append("word_layout_split")
    return display[:split] + "\n" + display[split:], flags


def _nearest(value):
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _mapping(word, clip, rate, previous_end):
    lo, hi = Fraction(clip["source_in_frame"], 1) / rate, Fraction(clip["source_out_frame"], 1) / rate
    original_start, original_end = Fraction(str(word["start"])), Fraction(str(word["end"]))
    start, end = max(lo, original_start), min(hi, original_end)
    if start >= end:
        return None
    first = (start - lo) * rate + clip["timeline_in_frame"]
    last = (end - lo) * rate + clip["timeline_in_frame"]
    inside, outside = max(clip["timeline_in_frame"], _nearest(first)), min(clip["timeline_out_frame"], _nearest(last))
    expanded = outside <= inside
    if expanded:
        inside = min(clip["timeline_out_frame"] - 1, max(clip["timeline_in_frame"], first.numerator // first.denominator))
        outside = inside + 1
    return {"word_id": word["id"], "text": _text(word["text"]),
            "original_start_seconds": word["start"], "original_end_seconds": word["end"],
            "source_start_seconds": float(start), "source_end_seconds": float(end),
            "timeline_in_frame": inside, "timeline_out_frame": outside,
            "clipped_start": start != original_start, "clipped_end": end != original_end,
            "short_expanded": expanded, "overlaps_previous": previous_end is not None and original_start < previous_end}


def _event_ticks(cue, fps, scale):
    rate = frame_rate(fps)
    start = Fraction(cue["timeline_in_frame"] * scale, 1) / rate
    end = Fraction(cue["timeline_out_frame"] * scale, 1) / rate
    return -(-start.numerator // start.denominator), end.numerator // end.denominator


def _representable(cue, fps, scale):
    first, last = _event_ticks(cue, fps, scale)
    rate = frame_rate(fps)
    # A nonempty serialized interval can fall entirely between two frame PTS.
    lo, hi = Fraction(first, scale) * rate, Fraction(last, scale) * rate
    inside = max(cue["timeline_in_frame"], -(-lo.numerator // lo.denominator))
    outside = min(cue["timeline_out_frame"], -(-hi.numerator // hi.denominator))
    return first < last and inside < outside


def _span(group, rate):
    return Fraction(max(word["timeline_out_frame"] for word in group)
                    - min(word["timeline_in_frame"] for word in group), 1) / rate


def _group_columns(group):
    return _columns("".join(word["text"].replace("\n", " ") for word in group).strip())


def _short_group(group, rate, policy):
    return (_group_columns(group) <= 2 * policy["orphan_tail_max_cjk_characters"]
            and _span(group, rate) <= Fraction(str(policy["orphan_tail_max_seconds"])))


def _rebalance(groups, sentence, rate, policy):
    """Remove greedy split leftovers within one sentence/pause, preserving words."""
    result = []
    capacity = 2 * policy["target_cjk_characters_per_line"] * policy["max_lines"]
    for group in groups:
        if result and _short_group(group, rate, policy):
            previous, previous_flags = result[-1]
            gap = Fraction(str(group[0]["source_start_seconds"])) - max(
                Fraction(str(word["source_end_seconds"])) for word in previous)
            same_sentence = len({sentence[word["word_id"]] for word in previous + group}) == 1
            if same_sentence and gap < Fraction(str(policy["pause_seconds"])):
                merged = previous + group
                if (_span(merged, rate) <= Fraction(str(policy["orphan_merge_max_seconds"]))
                        and _group_columns(merged) <= capacity):
                    result[-1] = (merged, previous_flags + ["orphan_tail_merged"])
                    continue
                for count in (1, 2):
                    if len(previous) <= count:
                        continue
                    left, right = previous[:-count], previous[-count:] + group
                    if (right[0]["timeline_in_frame"] < max(word["timeline_out_frame"] for word in left)
                            or _short_group(left, rate, policy) or _short_group(right, rate, policy)
                            or max(_group_columns(left), _group_columns(right)) > capacity
                            or max(_span(left, rate), _span(right, rate)) > policy["max_cue_seconds"]):
                        continue
                    result[-1] = (left, previous_flags + ["orphan_tail_rebalanced"])
                    result.append((right, ["orphan_tail_rebalanced"]))
                    break
                else:
                    result.append((group, []))
                continue
        result.append((group, []))
    return result


def _cue(group, clip, index, group_index, rate, group_flags, policy):
    text, flags = _layout(group, policy)
    flags.extend(group_flags)
    first, last = min(word["timeline_in_frame"] for word in group), max(word["timeline_out_frame"] for word in group)
    if Fraction(last - first, 1) / rate > policy["max_cue_seconds"]:
        flags.append("duration_target_exception" if "orphan_tail_merged" in flags else "duration_limit_exception")
    if _short_group(group, rate, policy):
        flags.append("short_cue_exception")
    if any(word["overlaps_previous"] for word in group):
        flags.append("overlap_merged")
    return {"id": f"cue_{index:04d}_{group_index:04d}", "clip_id": clip["id"], "source_id": clip["source_id"],
            "word_ids": [word["word_id"] for word in group],
            "source_start_seconds": min(word["source_start_seconds"] for word in group),
            "source_end_seconds": max(word["source_end_seconds"] for word in group),
            "timeline_in_frame": first, "timeline_out_frame": last, "text": text,
            "word_mappings": group, "flags": list(dict.fromkeys(flags))}


def build_subtitles(edl, analyses: dict) -> dict:
    """Build deterministic subtitles without changing cuts, words or ASR timing."""
    validate_edl(edl)
    expected_ids = {source["id"] for source in edl["sources"]}
    if not isinstance(analyses, dict) or set(analyses) != expected_ids:
        raise ValueError("analyses must contain exactly every EDL source id")
    rate = frame_rate(edl["profile"]["fps"])
    policy = deepcopy(PORTRAIT_POLICY if edl["schema_version"] == "0.2.0" else POLICY)
    sources, cues, reports = [], [], []
    analysis_schema = _schema("analysis.schema.json")
    for source in edl["sources"]:
        analysis = analyses[source["id"]]
        validate_analysis(analysis, analysis_schema)
        media = analysis["media"]
        if (media["normalized_sha256"] != source["sha256"] or media["time_reference"] != "normalized_video"
                or media["has_audio"] != (source["audio_stream_index"] is not None)
                or abs(Fraction(str(media["duration_seconds"])) * rate - source["total_frames"]) > 1):
            raise ValueError("analysis normalized hash/time/audio/duration differs from EDL source")
        sources.append({"source_id": source["id"], "source_sha256": source["sha256"],
                        "analysis_sha256": _digest(analysis), "has_audio": media["has_audio"],
                        "analysis_result_kind": analysis["result_kind"]})
    for index, clip in enumerate(edl["clips"], 1):
        analysis = analyses[clip["source_id"]]
        sentence = {word_id: row["id"] for row in analysis["sentences"] for word_id in row["word_ids"]}
        mappings, excluded, previous_end = [], [], None
        for word in analysis["words"]:
            mapped = _mapping(word, clip, rate, previous_end)
            if mapped is None:
                excluded.append(word["id"])
                continue
            mappings.append(mapped)
            previous_end = max(previous_end or Fraction(0), Fraction(str(word["end"])))
        groups, group = [], []
        for word in mappings:
            if group:
                current_end = max(row["timeline_out_frame"] for row in group)
                proposed_span = max(current_end, word["timeline_out_frame"]) - group[0]["timeline_in_frame"]
                gap = Fraction(str(word["source_start_seconds"])) - max(Fraction(str(row["source_end_seconds"])) for row in group)
                sentence_break = sentence[word["word_id"]] != sentence[group[-1]["word_id"]]
                length_break = (_columns("".join(row["text"].replace("\n", " ") for row in group + [word]))
                                > 2 * policy["target_cjk_characters_per_line"] * policy["max_lines"])
                if (word["timeline_in_frame"] >= current_end and
                        (sentence_break or gap >= Fraction(str(policy["pause_seconds"]))
                         or Fraction(proposed_span, 1) / rate > policy["max_cue_seconds"] or length_break)):
                    groups.append(group)
                    group = []
            group.append(word)
        if group:
            groups.append(group)
        clip_cues = [_cue(group, clip, index, number, rate, flags, policy)
                     for number, (group, flags) in enumerate(_rebalance(groups, sentence, rate, policy), 1)]
        cues.extend(clip_cues)
        reports.append({**{key: clip[key] for key in ("source_id", "source_in_frame", "source_out_frame",
                                                     "timeline_in_frame", "timeline_out_frame")}, "clip_id": clip["id"],
                        "status": "no_audio" if not analysis["media"]["has_audio"] else "cues" if clip_cues else "no_words_in_clip",
                        "selected_word_ids": [word["word_id"] for word in mappings], "excluded_word_ids": excluded,
                        "clipped_word_ids": [word["word_id"] for word in mappings if word["clipped_start"] or word["clipped_end"]],
                        "short_expanded_word_ids": [word["word_id"] for word in mappings if word["short_expanded"]],
                        "overlapping_word_ids": [word["word_id"] for word in mappings if word["overlaps_previous"]],
                        "cue_ids": [cue["id"] for cue in clip_cues],
                        "ass_unrepresentable_cue_ids": [cue["id"] for cue in clip_cues
                                                       if not _representable(cue, edl["profile"]["fps"], 100)]})
    plan = {"schema_version": "0.1.0", "artifact_kind": "edl_subtitles", "edl_sha256": _digest(edl),
            "profile": deepcopy(edl["profile"]), "fps": deepcopy(edl["profile"]["fps"]),
            "duration_frames": edl["duration_frames"], "sources": sources, "policy": policy,
            "brand": deepcopy(BRAND), "cues": cues, "clip_reports": reports}
    _validate_plan(plan)
    return plan


def _validate_plan(plan):
    _strict_integers(plan)
    _schema_validate(plan, _schema("subtitles.schema.json"))
    frame_rate(plan["fps"])
    if plan["profile"]["fps"] != plan["fps"]:
        raise ValueError("subtitle profile fps differs from cue fps")
    if (plan["policy"] == PORTRAIT_POLICY and
            (plan["profile"]["width"] % 2 or plan["profile"]["height"] % 2
             or plan["profile"]["width"] * 16 != plan["profile"]["height"] * 9)):
        raise ValueError("portrait subtitle policy requires an even exact 9:16 output profile")
    sources = {row["source_id"] for row in plan["sources"]}
    reports = {row["clip_id"]: row for row in plan["clip_reports"]}
    if len(sources) != len(plan["sources"]) or len(reports) != len(plan["clip_reports"]):
        raise ValueError("duplicate subtitle source/clip id")
    cursor, cue_ids = 0, set()
    for report in plan["clip_reports"]:
        if (report["source_id"] not in sources or report["source_in_frame"] >= report["source_out_frame"]
                or report["timeline_in_frame"] != cursor
                or report["timeline_out_frame"] - cursor != report["source_out_frame"] - report["source_in_frame"]):
            raise ValueError("subtitle clip reports must match a sequential half-open timeline")
        cursor = report["timeline_out_frame"]
        members = [cue for cue in plan["cues"] if cue["clip_id"] == report["clip_id"]]
        if [cue["id"] for cue in members] != report["cue_ids"] or [wid for cue in members for wid in cue["word_ids"]] != report["selected_word_ids"]:
            raise ValueError("subtitle cue/word accounting differs from clip report")
    if cursor != plan["duration_frames"]:
        raise ValueError("subtitle duration differs from clip reports")
    previous = 0
    for cue in plan["cues"]:
        report = reports.get(cue["clip_id"])
        if (cue["id"] in cue_ids or report is None or cue["source_id"] != report["source_id"]
                or not report["timeline_in_frame"] <= cue["timeline_in_frame"] < cue["timeline_out_frame"] <= report["timeline_out_frame"]
                or cue["timeline_in_frame"] < previous):
            raise ValueError("subtitle cue is duplicate, overlapping or outside its selected clip")
        mappings = cue["word_mappings"]
        if (cue["word_ids"] != [word["word_id"] for word in mappings]
                or cue["timeline_in_frame"] != min(word["timeline_in_frame"] for word in mappings)
                or cue["timeline_out_frame"] != max(word["timeline_out_frame"] for word in mappings)
                or cue["source_start_seconds"] != min(word["source_start_seconds"] for word in mappings)
                or cue["source_end_seconds"] != max(word["source_end_seconds"] for word in mappings)
                or cue["text"] != _layout(mappings, plan["policy"])[0]):
            raise ValueError("subtitle cue differs from its original-word mapping evidence")
        for word in mappings:
            if not (word["original_start_seconds"] < word["original_end_seconds"]
                    and word["source_start_seconds"] < word["source_end_seconds"]
                    and cue["timeline_in_frame"] <= word["timeline_in_frame"] < word["timeline_out_frame"] <= cue["timeline_out_frame"]):
                raise ValueError("subtitle word mapping has invalid half-open bounds")
        _text(cue["text"])
        cue_ids.add(cue["id"])
        previous = cue["timeline_out_frame"]


def validate_subtitles(plan, edl, analyses):
    """Validate schema and rebuild every cue/evidence/hash from the original inputs."""
    _validate_plan(plan)
    if _canonical(plan) != _canonical(build_subtitles(edl, analyses)):
        raise ValueError("subtitle plan differs from deterministic EDL/analysis reconstruction")


def _timestamp(ticks, scale):
    seconds, fraction = divmod(ticks, scale)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if scale == 1000:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{fraction:03d}"
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def _times(cue, plan, scale):
    first, last = _event_ticks(cue, plan["fps"], scale)
    if not _representable(cue, plan["fps"], scale):
        raise ValueError(f"cue {cue['id']} cannot be represented at {'ASS centisecond' if scale == 100 else 'SRT millisecond'} precision without crossing its clip")
    return _timestamp(first, scale), _timestamp(last, scale)


def to_srt(plan) -> str:
    _validate_plan(plan)
    blocks = []
    for number, cue in enumerate(plan["cues"], 1):
        start, end = _times(cue, plan, 1000)
        text = cue["text"].translate(str.maketrans({"<": "＜", ">": "＞"}))
        blocks.append(f"{number}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def _fit_font(text, requested, available_width):
    # Conservative CJK-em estimate with 15% glyph-width room; controlled numeric
    # sizing never interprets raw ASS override syntax. Impossible fits fail.
    # A wide Latin glyph such as W can approach one em despite occupying one
    # terminal column. Use one em per non-combining glyph rather than columns/2.
    units = max((sum(not unicodedata.combining(char) for char in line) for line in text.split("\n")), default=0)
    fitted = min(requested, available_width * 20 // (max(1, units) * 23))
    if fitted < 1:
        raise ValueError("portrait subtitle text cannot fit the output width at a positive font size")
    return fitted


def validate_font_name(font_name):
    """Accept one literal font family shared by the ASS and MLT exporters."""
    if (not isinstance(font_name, str) or not font_name.strip() or len(font_name) > 128
            or any(char in ",\\{}" or ord(char) < 32 for char in font_name)):
        raise ValueError("ASS font_name must be one literal font family without control/CSV characters")
    return font_name


def subtitle_layout(plan):
    """Return shared pixel layout without quantizing integer-frame cue ranges."""
    _validate_plan(plan)
    width, height = plan["profile"]["width"], plan["profile"]["height"]
    margin_x, margin_y = max(2, round(width * .05)), max(2, round(height * .06))
    subtitle_size, brand_size = max(8, round(height * .045)), max(8, round(height * .027))
    portrait = plan["policy"] == PORTRAIT_POLICY
    available_width = width - 2 * margin_x - 4  # Include the one-pixel outline.
    if portrait:
        subtitle_size = _fit_font("字" * plan["policy"]["target_cjk_characters_per_line"],
                                  max(1, min(round(height * .045), (height - 2 * margin_y) // 3)), available_width)
        brand_size = _fit_font(plan["brand"]["text"], max(1, round(height * .027)), available_width)
    return {"width": width, "height": height, "margin_x": margin_x, "margin_y": margin_y,
            "subtitle_size": subtitle_size, "brand_size": brand_size,
            "available_width": available_width, "portrait": portrait}


def to_ass(plan, *, font_name="Noto Sans CJK SC") -> str:
    validate_font_name(font_name)
    layout = subtitle_layout(plan)
    width, height = layout["width"], layout["height"]
    margin_x, margin_y = layout["margin_x"], layout["margin_y"]
    subtitle_size, brand_size = layout["subtitle_size"], layout["brand_size"]
    available_width, portrait = layout["available_width"], layout["portrait"]
    lines = ["[Script Info]", "Title: ClipForge EDL subtitles", "ScriptType: v4.00+",
             f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for style, size, alignment in (("Subtitle", subtitle_size, 2), ("Brand", brand_size, 9)):
        lines.append(f"Style: {style},{font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                     f"0,0,0,0,100,100,0,0,1,1,0,{alignment},{margin_x},{margin_x},{margin_y},1")
    lines.extend(["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"])
    end = Fraction(plan["duration_frames"] * 100, 1) / frame_rate(plan["fps"])
    # The global label extends to the next centisecond so every last video frame
    # retains the label; word cues themselves always quantize inward.
    label_end = _timestamp(-(-end.numerator // end.denominator), 100)
    lines.append(f"Dialogue: 1,0:00:00.00,{label_end},Brand,ClipForge,0,0,0,,AI辅助剪辑")
    literals = str.maketrans({"\\": "＼", "{": "｛", "}": "｝"})
    for cue in plan["cues"]:
        start, finish = _times(cue, plan, 100)
        text = cue["text"].translate(literals)
        size = _fit_font(text, subtitle_size, available_width) if portrait else subtitle_size
        text = text.replace("\n", r"\N")
        if size < subtitle_size:
            text = "{\\fs" + str(size) + "}" + text
        lines.append(f"Dialogue: 0,{start},{finish},Subtitle,{cue['id']},0,0,0,,{text}")
    return "\n".join(lines) + "\n"
