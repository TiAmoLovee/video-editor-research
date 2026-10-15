"""Explicit candidate selection -> versioned integer-frame EDL, offline only.

Source metadata must describe measured video frames from the normalized media;
container duration and average frame rate alone do not establish this contract.
All ranges are half-open. Baselines record selection, not semantic acceptance.
"""

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
VERSION = "0.1.0"
PORTRAIT_VERSION = "0.2.0"
PORTRAIT_FIT_VERSION = "0.3.0"
MAX_FRAMES = 2147483647
INTEGER_FIELDS = {
    "num", "den", "width", "height", "total_frames", "duration_frames",
    "sample_aspect_num", "sample_aspect_den", "rotation_degrees", "colorspace",
    "video_stream_index", "audio_stream_index", "source_in_frame", "source_out_frame",
    "timeline_in_frame", "timeline_out_frame", "original_in_frame", "original_out_frame",
    "original_selection_index", "x", "y",
}


def _numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN/Infinity is not a valid JSON number")
    if isinstance(value, dict):
        for key, item in value.items():
            if key in INTEGER_FIELDS and item is not None and type(item) is not int:
                raise ValueError(f"{key}: requires a JSON integer, not float/bool")
            _numbers(item)
    elif isinstance(value, list):
        for item in value:
            _numbers(item)


def _schema_validate(value, schema):
    _numbers(value)
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error is not None:
        raise ValueError(f"{error.json_path}: {error.message}")


def _load_schema(version=VERSION):
    filenames = {VERSION: "edl.schema.json", PORTRAIT_VERSION: "edl-v0.2.schema.json",
                 PORTRAIT_FIT_VERSION: "edl-v0.3.schema.json"}
    if not isinstance(version, str) or version not in filenames:
        raise ValueError("unsupported EDL schema_version")
    return json.loads((ROOT / "schemas" / filenames[version]).read_text(encoding="utf-8"))


def frame_rate(fps):
    """Require a canonical, positive rational rate representable by MLT."""
    if (not isinstance(fps, dict) or set(fps) != {"num", "den"}
            or any(type(fps[k]) is not int or not 1 <= fps[k] <= MAX_FRAMES
                   for k in ("num", "den"))):
        raise ValueError("fps requires positive integer num/den")
    rate = Fraction(fps["num"], fps["den"])
    if (rate.numerator, rate.denominator) != (fps["num"], fps["den"]):
        raise ValueError("fps must be a reduced rational fraction")
    if not 1 <= rate <= 120:
        raise ValueError("prototype supports fps from 1 to 120 only")
    return rate


def seconds_to_frame(seconds, fps):
    """Nearest frame, half up; decimal JSON values avoid binary-float ties."""
    if (type(seconds) not in (int, float) or seconds < 0
            or (isinstance(seconds, float) and not math.isfinite(seconds))):
        raise ValueError("seconds must be a finite nonnegative JSON number")
    position = Fraction(str(seconds)) * frame_rate(fps)
    frame = (2 * position.numerator + position.denominator) // (2 * position.denominator)
    if frame > MAX_FRAMES:
        raise ValueError("frame position exceeds the MLT integer range")
    return frame


def _local_ref(ref):
    """Validate local file references without fetching or opening media."""
    if not isinstance(ref, str) or not ref.strip() or ref != ref.strip():
        raise ValueError("source ref must be a nonempty local file path")
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
           or ord(char) in (0xFFFE, 0xFFFF) for char in ref):
        raise ValueError("source ref contains invalid XML/path characters")
    normalized = ref.replace("\\", "/")
    if normalized.startswith("//") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized):
        if not re.match(r"^[A-Za-z]:/", normalized):
            raise ValueError("network, URI and drive-relative source refs are unsupported")
        if normalized.startswith("//"):
            raise ValueError("network source refs are unsupported")
    if any(part == ".." for part in PureWindowsPath(ref).parts):
        raise ValueError("source ref may not traverse parent directories")
    # Colons beyond a drive prefix include Windows alternate data streams.
    tail = normalized[2:] if re.match(r"^[A-Za-z]:/", normalized) else normalized
    if ":" in tail:
        raise ValueError("source ref must identify an ordinary local file")


def _profile_sources(profile, sources, *, native_dimensions=True):
    rate = frame_rate(profile["fps"])
    ids = set()
    for source in sources:
        if source["id"] in ids:
            raise ValueError("duplicate source id")
        ids.add(source["id"])
        _local_ref(source["ref"])
        if (frame_rate(source["fps"]) != rate or native_dimensions
                and (source["width"] != profile["width"] or source["height"] != profile["height"])):
            raise ValueError("source fps/dimensions must match the project profile")
        if source["audio_stream_index"] == source["video_stream_index"]:
            raise ValueError("audio and video stream indices must be different")


def _center_crop(source):
    """Largest exact 9:16 even rectangle; center is chroma aligned within 1px."""
    if source["width"] % 2 or source["height"] % 2:
        raise ValueError("portrait sources require even native dimensions")
    k = min(source["width"] // 9, source["height"] // 16) // 2 * 2
    if k < 2:
        raise ValueError("source is too small for an even 9:16 crop")
    width, height = 9 * k, 16 * k
    return {"source_id": source["id"], "x": ((source["width"] - width) // 2) // 2 * 2,
            "y": ((source["height"] - height) // 2) // 2 * 2, "width": width, "height": height}


def _fit_pad(source, profile):
    """Fit the complete source on a black canvas, rounding to even pixels.

    Each scaled dimension is rounded to its nearest even integer, with ties up.
    The even origin approximates the true center within one pixel. These are
    output-canvas coordinates, never a rectangle to crop out of the source.
    """
    if source["width"] % 2 or source["height"] % 2:
        raise ValueError("portrait sources require even native dimensions")
    scale = min(Fraction(profile["width"], source["width"]),
                Fraction(profile["height"], source["height"]))

    def dimension(native, limit):
        value = native * scale
        rounded = 2 * ((value.numerator + value.denominator) // (2 * value.denominator))
        return max(2, min(limit, rounded))

    width = dimension(source["width"], profile["width"])
    height = dimension(source["height"], profile["height"])
    return {"source_id": source["id"], "x": ((profile["width"] - width) // 2) // 2 * 2,
            "y": ((profile["height"] - height) // 2) // 2 * 2, "width": width, "height": height}


def _portrait_framing(data):
    profile = data["profile"]
    if (profile["width"] % 2 or profile["height"] % 2
            or profile["width"] * 16 != profile["height"] * 9):
        raise ValueError("portrait output requires even exact 9:16 dimensions")
    # Native measurements retain the 0.1 source declaration contract.
    original_schema = _load_schema()
    for source in data["sources"]:
        _schema_validate(source, {"$ref": "#/$defs/source", "$defs": original_schema["$defs"]})
    fit = data["schema_version"] == PORTRAIT_FIT_VERSION
    expected = ({"mode": "fit_pad", "background": "black",
                 "sources": [_fit_pad(source, profile) for source in data["sources"]]} if fit else
                {"mode": "center_crop", "sources": [_center_crop(source) for source in data["sources"]]})
    if data["framing"] != expected:
        raise ValueError("framing must cover every source with its canonical even fit on black" if fit else
                         "framing must cover every source with its canonical even center crop")


def create_portrait_edl(edl, width=1080, height=1920, *, mode="center_crop"):
    """Derive explicit 9:16 framing while retaining every source/cut fact.

    The default center crop retains the existing EDL 0.2.0 contract. Full-frame
    fitting with black padding is a separate EDL 0.3.0 contract.
    """
    validate_edl(edl)
    if edl["schema_version"] != VERSION:
        raise ValueError("portrait conversion requires an EDL 0.1.0 input")
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 16384
            or not 1 <= height <= 16384 or width % 2 or height % 2 or width * 16 != height * 9):
        raise ValueError("portrait output requires even exact 9:16 JSON integer dimensions")
    if mode not in ("center_crop", "fit_pad"):
        raise ValueError("portrait mode must be center_crop or fit_pad")
    result = deepcopy(edl)
    result["schema_version"] = PORTRAIT_FIT_VERSION if mode == "fit_pad" else PORTRAIT_VERSION
    result["profile"] = {"width": width, "height": height, "fps": deepcopy(edl["profile"]["fps"])}
    result["framing"] = ({"mode": "fit_pad", "background": "black",
                          "sources": [_fit_pad(source, result["profile"]) for source in edl["sources"]]}
                         if mode == "fit_pad" else
                         {"mode": "center_crop", "sources": [_center_crop(source) for source in edl["sources"]]})
    validate_edl(result)
    return result


def validate_edl(data):
    """Validate schema and cross-field invariants; raise ValueError on failure."""
    if not isinstance(data, dict):
        raise ValueError("EDL must be an object")
    version = data.get("schema_version")
    schema = _load_schema(version)
    _schema_validate(data, schema)
    _profile_sources(data["profile"], data["sources"], native_dimensions=version == VERSION)
    if version in (PORTRAIT_VERSION, PORTRAIT_FIT_VERSION):
        _portrait_framing(data)
    sources = {source["id"]: source for source in data["sources"]}
    clip_ids, baseline_ids, baseline_positions = set(), set(), set()
    cursor = 0
    for clip in data["clips"]:
        if clip["id"] in clip_ids:
            raise ValueError("duplicate clip id")
        clip_ids.add(clip["id"])
        if clip["source_id"] not in sources:
            raise ValueError("clip references an unknown source id")
        source = sources[clip["source_id"]]
        start, end = clip["source_in_frame"], clip["source_out_frame"]
        if not 0 <= start < end <= source["total_frames"]:
            raise ValueError("clip requires 0 <= source in < out <= total_frames")
        if (clip["timeline_in_frame"] != cursor
                or clip["timeline_out_frame"] != cursor + end - start):
            raise ValueError("timeline must be sequential and gap-free with matching durations")
        cursor = clip["timeline_out_frame"]
        baseline = clip["provenance"]
        if (baseline["original_source_id"] != clip["source_id"]
                or baseline["original_source_sha256"] != source["sha256"]):
            raise ValueError("candidate baseline must remain bound to its original source id/hash")
        key = (clip["source_id"], baseline["candidate_document_sha256"], baseline["candidate_id"])
        position = (baseline["candidate_document_sha256"], baseline["original_selection_index"])
        if key in baseline_ids or position in baseline_positions:
            raise ValueError("duplicate candidate baseline or original selection index")
        baseline_ids.add(key)
        baseline_positions.add(position)
        original_start = baseline["original_start_seconds"]
        original_end = baseline["original_end_seconds"]
        source_seconds = Fraction(source["total_frames"], 1) / frame_rate(source["fps"])
        if not 0 <= Fraction(str(original_start)) < Fraction(str(original_end)) <= source_seconds:
            raise ValueError("original candidate seconds exceed the video frame bounds")
        original_in = seconds_to_frame(original_start, source["fps"])
        original_out = seconds_to_frame(original_end, source["fps"])
        if (not original_in < original_out or baseline["original_in_frame"] != original_in
                or baseline["original_out_frame"] != original_out):
            raise ValueError("candidate baseline frames disagree with original seconds")
    if cursor != data["duration_frames"]:
        raise ValueError("duration_frames does not match the timeline")


def candidates_to_edl(candidate_document, selected_ids, source, profile=None):
    """Import one source's explicit IDs in the chosen order, without scoring.

Only the candidate projection needed for editing is consumed. The existing
analysis/scoring validators remain responsible for the input's earlier stages.
"""
    schema = _load_schema()
    if not isinstance(source, dict):
        raise ValueError("source metadata must be an object")
    profile = deepcopy(profile if profile is not None else {
        "width": source.get("width"), "height": source.get("height"), "fps": source.get("fps")})
    for value, definition in ((profile, "profile"), (source, "source")):
        _schema_validate(value, {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]})
    _profile_sources(profile, [source])
    if (not isinstance(selected_ids, list) or not selected_ids
            or any(not isinstance(item, str) or not item for item in selected_ids)):
        raise ValueError("select at least one explicit candidate id in output order")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("duplicate selected candidate id")
    if not isinstance(candidate_document, dict):
        raise ValueError("candidate document must be an object")
    _numbers(candidate_document)
    media = candidate_document.get("media", {})
    if not isinstance(media, dict) or (media.get("time_reference") != "normalized_video"
            or media.get("normalized_sha256") != source["sha256"]
            or type(media.get("has_audio")) is not bool
            or media["has_audio"] != (source["audio_stream_index"] is not None)):
        raise ValueError("candidate media must match the normalized source hash/time reference/audio")
    duration = media.get("duration_seconds")
    if (type(duration) not in (int, float) or duration <= 0
            or (isinstance(duration, float) and not math.isfinite(duration))):
        raise ValueError("candidate media duration_seconds must be finite and positive")
    items = candidate_document.get("candidates")
    if not isinstance(items, list):
        raise ValueError("candidate document requires a candidates array")
    candidates = {}
    source_seconds = Fraction(source["total_frames"], 1) / frame_rate(source["fps"])
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
                or item["id"] in candidates):
            raise ValueError("missing or duplicate candidate id")
        start, end = item.get("start"), item.get("end")
        for seconds in (start, end):
            seconds_to_frame(seconds, source["fps"])
        if not 0 <= Fraction(str(start)) < Fraction(str(end)) <= min(Fraction(str(duration)), source_seconds):
            raise ValueError("candidate seconds exceed the declared media/video frame bounds")
        refs = item.get("source_sentences", [])
        if (not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs)
                or len(set(refs)) != len(refs)):
            raise ValueError("source_sentences must contain unique nonempty ids")
        candidates[item["id"]] = item
    if any(identifier not in candidates for identifier in selected_ids):
        raise ValueError("selected candidate id does not exist")
    digest = hashlib.sha256(json.dumps(candidate_document, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    clips, cursor = [], 0
    for index, identifier in enumerate(selected_ids):
        candidate = candidates[identifier]
        start = seconds_to_frame(candidate["start"], source["fps"])
        end = seconds_to_frame(candidate["end"], source["fps"])
        if not start < end:
            raise ValueError("selected candidate quantizes to zero video frames")
        clips.append({"id": f"clip_{identifier}", "source_id": source["id"],
                      "source_in_frame": start, "source_out_frame": end,
                      "timeline_in_frame": cursor, "timeline_out_frame": cursor + end - start,
                      "provenance": {"candidate_id": identifier, "candidate_document_sha256": digest,
                                     "original_source_id": source["id"],
                                     "original_source_sha256": source["sha256"],
                                     "original_start_seconds": candidate["start"],
                                     "original_end_seconds": candidate["end"],
                                     "original_in_frame": start, "original_out_frame": end,
                                     "original_selection_index": index,
                                     "source_sentences": deepcopy(candidate.get("source_sentences", []))}})
        cursor += end - start
    result = {"schema_version": VERSION, "artifact_kind": "edit_decision_list", "profile": profile,
              "sources": [deepcopy(source)], "clips": clips, "duration_frames": cursor}
    validate_edl(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--media-profile", required=True, type=Path,
                        help="JSON with profile and source objects matching edl.schema.json")
    parser.add_argument("--selected-id", action="append", required=True,
                        help="repeat in the desired output order; no automatic acceptance")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.media_profile.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or set(manifest) != {"profile", "source"}:
            raise ValueError("media profile manifest requires exactly profile and source")
        data = candidates_to_edl(json.loads(args.candidates.read_text(encoding="utf-8-sig")),
                                 args.selected_id, manifest["source"], manifest["profile"])
        content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content)
    except (OSError, ValueError) as error:
        parser.exit(1, f"INVALID: {error}\n")
    print(f"EDL: {args.output} ({len(data['clips'])} clips; {data['duration_frames']} frames)")


if __name__ == "__main__":
    main()
