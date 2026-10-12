"""Audit saved Shotcut case semantics without rendering or modifying inputs.

Run this file after GUI saves. Missing saved projects remain pending. The output
is a static semantic audit, to be paired with the operator's actual GUI evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path):
    path = Path(path)
    return {"path": str(path.resolve()), "sha256": sha(path), "bytes": path.stat().st_size}


def properties(element):
    result = {}
    for item in element.findall("property"):
        key = item.get("name")
        require(key and key not in result, f"Duplicate or unnamed property: {key}")
        result[key] = item.text or ""
    return result


def numeric(value):
    number = Fraction(str(value).strip())
    return str(number.numerator) if number.denominator == 1 else str(number)


def css(value):
    result = {}
    for declaration in value.split(";"):
        if declaration.strip():
            key, separator, setting = declaration.partition(":")
            require(separator, f"Invalid CSS declaration: {declaration}")
            result[key.strip().lower()] = setting.strip()
    return result


class RichText(HTMLParser):
    """Extract literal text and explicit rich formatting, not XML/HTML bytes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.stack = []
        self.parts = []
        self.runs = []
        self.paragraphs = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "body":
            self.in_body = True
        if not self.in_body:
            return
        inherited = dict(self.stack[-1][1]) if self.stack else {}
        inherited.update(css(attributes.get("style", "")))
        if "face" in attributes:
            inherited["font-family"] = attributes["face"]
        if tag == "p":
            self.paragraphs.append({"align": attributes.get("align", ""),
                                    "style": css(attributes.get("style", ""))})
        if tag == "br":
            self.parts.append("\n")
            self.add_run("\n", inherited)
        elif tag not in ("meta", "img", "hr", "input"):
            self.stack.append((tag, inherited))

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        if tag not in ("br", "meta", "img", "hr", "input"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.in_body:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break
        if tag == "body":
            self.in_body = False

    def add_run(self, text, style):
        relevant = {key: value for key, value in style.items()
                    if key.startswith("font-") or key in ("color", "text-decoration")}
        if self.runs and self.runs[-1]["style"] == relevant:
            self.runs[-1]["text"] += text
        else:
            self.runs.append({"text": text, "style": relevant})

    def handle_data(self, data):
        if self.in_body:
            self.parts.append(data)
            self.add_run(data, self.stack[-1][1] if self.stack else {})

    def semantic(self):
        return {"text": "".join(self.parts), "runs": self.runs,
                "paragraphs": self.paragraphs}


class Project:
    def __init__(self, path, source_hashes):
        self.path = Path(path).resolve()
        self.root = ET.parse(self.path).getroot()
        require(self.root.tag == "mlt", "Not an MLT project")
        self.ids = {}
        for node in self.root.iter():
            if node.get("id"):
                require(node.get("id") not in self.ids, "Duplicate graph ID")
                self.ids[node.get("id")] = node
        profile = self.root.find("profile")
        require(profile is not None, "Missing MLT profile")
        p = profile.attrib
        self.fps = Fraction(int(p["frame_rate_num"]), int(p["frame_rate_den"]))
        require(self.fps > 0, "Invalid fps")
        self.profile = {"width": int(p["width"]), "height": int(p["height"]),
                        "fps": str(self.fps), "progressive": int(p["progressive"]),
                        "sample_aspect": str(Fraction(int(p["sample_aspect_num"]), int(p["sample_aspect_den"]))),
                        "display_aspect": str(Fraction(int(p["display_aspect_num"]), int(p["display_aspect_den"]))),
                        "colorspace": int(p["colorspace"])}
        self.time_records = []
        self.qtext_range_records = []
        self.source_hashes = source_hashes
        self.active_nodes = set()
        self.active_filters = set()
        project_root = self.root.get("root")
        self.media_base = Path(project_root) if project_root else self.path.parent
        if not self.media_base.is_absolute():
            self.media_base = self.path.parent / self.media_base

    def frame(self, value, label):
        value = "0" if value is None or value == "" else value
        if re.fullmatch(r"\d+", value):
            return int(value)
        match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", value)
        require(match is not None, f"Unsupported time expression {label}: {value}")
        require(int(match[2]) < 60 and Fraction(match[3]) < 60, f"Invalid clock: {value}")
        seconds = Fraction(match[1]) * 3600 + Fraction(match[2]) * 60 + Fraction(match[3])
        exact = seconds * self.fps
        shifted = exact + Fraction(1, 2)
        rounded = shifted.numerator // shifted.denominator
        residual = exact - rounded
        require(abs(residual) < Fraction(1, 2), f"Ambiguous half-frame clock: {label}")
        self.time_records.append({"label": label, "serialized": value, "frame": rounded,
                                  "signed_residual_frames": float(residual),
                                  "signed_residual_seconds": float(residual / self.fps)})
        return rounded

    def range(self, node, label, default_end=None):
        start = self.frame(node.get("in"), label + ".in")
        end = self.frame(node.get("out"), label + ".out") + 1 if node.get("out") is not None else default_end
        require(end is not None and end > start, f"Invalid or absent range: {label}")
        return [start, end]

    def animated_numeric(self, value, label, geometry=False):
        def scalar(item):
            return [numeric(v) for v in item.split()] if geometry else numeric(item)
        if "=" not in value:
            return scalar(value)
        result = []
        for part in value.split(";"):
            key, separator, setting = part.partition("=")
            require(separator, f"Invalid animation: {label}")
            interpolation = key[-1:] if key[-1:] in ("~", "|") else ""
            key = key[:-1] if interpolation else key
            result.append([self.frame(key, label + ".key"), interpolation, scalar(setting)])
        return result

    def resolve_resource(self, value):
        require(not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value), "Unexpected non-file resource")
        path = Path(value)
        if not path.is_absolute():
            path = self.media_base / path
        path = path.resolve()
        require(path.is_file(), f"Source media missing: {path}")
        canonical = os.path.normcase(str(path))
        if canonical not in self.source_hashes:
            self.source_hashes[canonical] = {"path": str(path), "sha256": sha(path),
                                             "bytes": path.stat().st_size}
        return canonical

    def visible_ranges(self, opacity, start, end):
        """Evaluate scalar/linear/hold opacity at every integer output frame."""
        if isinstance(opacity, str):
            scalar = Fraction(opacity)
            require(0 <= scalar <= 1, "Opacity outside 0..1")
            return [[start, end]] if scalar > 0 else []
        require(opacity and all(a[0] < b[0] for a, b in zip(opacity, opacity[1:])),
                "Opacity keys are not strictly ordered")
        require(all(k[1] in ("", "|") and 0 <= Fraction(k[2]) <= 1 for k in opacity),
                "Unsupported smooth opacity or opacity outside 0..1")
        ranges = []
        for output_frame in range(start, end):
            local = output_frame - start
            preceding = [k for k in opacity if k[0] <= local]
            following = [k for k in opacity if k[0] > local]
            left = preceding[-1] if preceding else opacity[0]
            value = Fraction(left[2])
            if preceding and following and left[1] != "|":
                right = following[0]
                value += (Fraction(right[2]) - value) * Fraction(local - left[0], right[0] - left[0])
            if value > 0:
                if ranges and ranges[-1][1] == output_frame:
                    ranges[-1][1] += 1
                else:
                    ranges.append([output_frame, output_frame + 1])
        return ranges

    def filter_semantic(self, node, label, duration, context):
        self.active_filters.add(id(node))
        p = properties(node)
        service = p.get("mlt_service")
        semantic = {"service": service, "range": self.range(node, label, duration),
                    "disable": numeric(p.get("disable", "0"))}
        if context == "source" and service == "crop":
            semantic["crop"] = {key: numeric(p.get(key, "0")) for key in
                                ("active", "center", "use_profile", "left", "top", "right", "bottom")}
        elif context == "timeline" and service == "qtext":
            parser = RichText()
            parser.feed(p.get("html", ""))
            parser.close()
            rich = parser.semantic()
            require(rich["text"] == p.get("argument", ""), f"HTML literal differs from argument: {label}")
            opacity = self.animated_numeric(p.get("opacity", "1"), label + ".opacity")
            declared_range = list(semantic["range"])
            runtime_range = list(declared_range)
            # MLT treats out=0 as unset. Shotcut may omit it on save; the
            # exporter deliberately gates [0,1) with opacity 0=1;1=0.
            if node.get("out") is not None and self.frame(node.get("out"), label + ".zero_out") == 0:
                runtime_range[1] = duration
            effective = self.visible_ranges(opacity, *runtime_range)
            require(effective, f"Caption is invisible on every output frame: {label}")
            self.qtext_range_records.append({"text": rich["text"], "declared_range": declared_range,
                                             "runtime_range": runtime_range,
                                             "effective_visible_ranges": effective,
                                             "opacity": opacity})
            semantic.pop("range")
            semantic["effective_visible_ranges"] = effective
            semantic.update({"rich_text": rich,
                             "role": "brand" if p.get("clipforge:cue_id") == "clipforge_brand" or rich["text"] == "AI辅助剪辑" else "caption",
                             "geometry": self.animated_numeric(p["geometry"], label + ".geometry", True),
                             "opacity": opacity,
                             "typewriter": numeric(p.get("typewriter", "0")),
                             "pixel_ratio": numeric(p.get("pixel_ratio", "1")),
                             "overflow_y": numeric(p.get("overflow-y", "0")),
                             "background": p.get("bgcolour", "").lower(),
                             "halign": p.get("halign", ""), "valign": p.get("valign", ""),
                             "editor_filter": p.get("shotcut:filter", "")})
        else:
            raise AuditError(f"Unexpected {context} effect: {service}")
        return semantic

    def source(self, node, label):
        self.active_nodes.add(id(node))
        require(node.tag in ("chain", "producer"), f"Unsupported nested source graph: {node.tag}")
        p = properties(node)
        service = p.get("mlt_service")
        length = self.frame(p.get("length"), label + ".length")
        result = {"service": service, "range": self.range(node, label, length), "length": length,
                  "eof": p.get("eof", "pause"),
                  "ignore_points": numeric(p.get("ignore_points", "0")),
                  "test_audio": numeric(p.get("set.test_audio", "0")),
                  "test_image": numeric(p.get("set.test_image", "0")),
                  "mute_on_pause": numeric(p.get("mute_on_pause", "0"))}
        if service == "avformat":
            result.update({"resource": self.resolve_resource(p["resource"]),
                           "video_index": int(p["video_index"]), "audio_index": int(p["audio_index"])})
        elif service == "color":
            result["resource"] = p.get("resource")
        else:
            raise AuditError(f"Unexpected source service: {service}")
        result["filters"] = [self.filter_semantic(f, label + ".filter", length, "source")
                             for f in node.findall("filter")]
        return result

    def inspect(self):
        referenced = {n.get("producer") for n in self.root.iter() if n.tag in ("track", "entry")}
        terminals = [n for n in self.root.findall("tractor") if n.get("id") not in referenced
                     and properties(n).get("shotcut") == "1"]
        require(len(terminals) == 1, "Expected one terminal Shotcut timeline tractor")
        tractor = terminals[0]
        self.active_nodes.add(id(tractor))
        timeline_range = self.range(tractor, "timeline")
        require(timeline_range[0] == 0, "Timeline does not start at frame zero")
        duration = timeline_range[1]
        tracks = []
        for track_index, track in enumerate(tractor.findall("track")):
            playlist = self.ids.get(track.get("producer"))
            require(playlist is not None and playlist.tag == "playlist", "Timeline track must resolve to playlist")
            self.active_nodes.add(id(playlist))
            require(not playlist.findall("filter"), "Unexpected playlist effect")
            cursor = 0
            entries = []
            for entry_index, entry in enumerate(playlist):
                if entry.tag == "property":
                    continue
                require(entry.tag == "entry", "Timeline blank or unknown node introduced")
                source = self.ids.get(entry.get("producer"))
                require(source is not None, "Unresolved timeline source ID")
                label = f"track{track_index}.entry{entry_index}"
                source_semantic = self.source(source, label + ".source")
                source_range = self.range(entry, label, source_semantic["range"][1])
                require(source_range[0] >= source_semantic["range"][0] and source_range[1] <= source_semantic["range"][1],
                        "Entry outside source range")
                length = source_range[1] - source_range[0]
                entries.append({"timeline_range": [cursor, cursor + length], "source_range": source_range,
                                "source": source_semantic})
                cursor += length
            require(cursor == duration, "Track duration differs from terminal timeline")
            services = {e["source"]["service"] for e in entries}
            role = "background" if services == {"color"} else "video"
            if role == "video":
                require(services == {"avformat"}, "Mixed generator and media video track")
                require(properties(playlist).get("shotcut:video") == "1", "Missing video track role")
            tracks.append({"role": role, "hide": track.get("hide", "0") or "0", "entries": entries})
        require(len(tracks) == 2 and [t["role"] for t in tracks] == ["background", "video"],
                "Unexpected timeline track layout")
        transitions = []
        for transition in tractor.findall("transition"):
            p = properties(transition)
            require(p.get("mlt_service") == "mix", "Unexpected timeline transition")
            transitions.append({key: numeric(p.get(key, "0")) for key in
                                ("a_track", "b_track", "always_active", "sum", "disable")})
        texts = [self.filter_semantic(f, "timeline.text", duration, "timeline") for f in tractor.findall("filter")]
        # Ignore filter ID/DOM order while retaining duplicate cue multiplicity.
        texts.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        active_expected = sum(len(e["source"]["filters"]) for t in tracks for e in t["entries"]) + len(texts)
        require(active_expected >= len(self.active_filters), "Active filter accounting error")
        for node in self.root.iter():
            if node.tag in ("chain", "producer", "playlist", "tractor") and id(node) in self.active_nodes:
                require(all(id(f) in self.active_filters for f in node.findall("filter")), "Uninspected active filter")
        return {"profile": self.profile, "duration_frames": duration, "tracks": tracks,
                "transitions": transitions, "qtexts": texts}


def differences(original, saved, path=""):
    result = []
    if type(original) is not type(saved):
        return [{"field": path, "original": original, "saved": saved}]
    if isinstance(original, dict):
        for key in sorted(original.keys() | saved.keys()):
            label = f"{path}.{key}" if path else key
            if key not in original or key not in saved:
                result.append({"field": label, "original": original.get(key), "saved": saved.get(key)})
            else:
                result.extend(differences(original[key], saved[key], label))
    elif isinstance(original, list):
        if len(original) != len(saved):
            result.append({"field": path + ".length", "original": len(original), "saved": len(saved)})
        for index, (left, right) in enumerate(zip(original, saved)):
            result.extend(differences(left, right, f"{path}[{index}]"))
    elif original != saved:
        result.append({"field": path, "original": original, "saved": saved})
    return result


def audit_case(case, source_hashes):
    original = Path(case["project"])
    saved = Path(case["expected_saved_project"])
    result = {"case": case["case"], "label": case.get("label"), "status": "pending",
              "original_project": file_record(original), "expected_saved_project": str(saved)}
    try:
        require(result["original_project"]["sha256"] == case["project_sha256"], "Original project hash differs from case manifest")
        if not saved.is_file():
            result["reason"] = "Saved project has not appeared; GUI acceptance is pending."
            return result
        result["saved_project"] = file_record(saved)
        before = Project(original, source_hashes)
        after = Project(saved, source_hashes)
        original_semantic = before.inspect()
        saved_semantic = after.inspect()
        changes = differences(original_semantic, saved_semantic)
        require(original_semantic["duration_frames"] == case["frames"], "Manifest frame count differs from original")
        caption_count = sum(q["role"] == "caption" for q in original_semantic["qtexts"])
        require(caption_count == case["cues"], "Manifest cue count differs from original")
        result.update({"status": "failed" if changes else "passed", "semantic_changes": changes,
                       "original_semantic": original_semantic, "saved_semantic": saved_semantic,
                       "clip_count": len(saved_semantic["tracks"][1]["entries"]),
                       "caption_count": sum(q["role"] == "caption" for q in saved_semantic["qtexts"]),
                       "brand_count": sum(q["role"] == "brand" for q in saved_semantic["qtexts"]),
                       "saved_title": after.root.get("title"), "saved_mlt_version": after.root.get("version"),
                       "has_shotcut_save_signature": bool(re.match(r"Shotcut version \d", after.root.get("title", ""))),
                       "time_serialization": {"method": "rational fps; nearest integer half-up; residual strictly below 0.5 frame",
                           "max_absolute_residual_frames": max((abs(r["signed_residual_frames"]) for r in after.time_records), default=0),
                           "records": after.time_records},
                       "qtext_declared_runtime_and_effective_ranges": {"original": before.qtext_range_records,
                                                                         "saved": after.qtext_range_records},
                       "opacity_gate_method": "Every integer output frame evaluated; out=0 is unset in MLT; linear/hold numeric opacity gates preserved."})
        require(sha(original) == result["original_project"]["sha256"] and sha(saved) == result["saved_project"]["sha256"],
                "Input XML changed during audit")
        result["input_xml_unchanged_during_audit"] = True
    except (AuditError, OSError, ET.ParseError, KeyError, ValueError, ZeroDivisionError) as error:
        result.update({"status": "failed", "error": str(error)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=HERE / "gui-cases.json")
    parser.add_argument("--output", type=Path, default=HERE / "gui-roundtrip.json")
    args = parser.parse_args()
    manifest_record = file_record(args.manifest)
    payload = json.loads(args.manifest.read_text("utf-8-sig"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    require(cases and len({c["case"] for c in cases}) == len(cases), "No cases or duplicate case names")
    source_hashes = {}
    results = []
    for case in cases:
        try:
            results.append(audit_case(case, source_hashes))
        except (OSError, KeyError, ValueError) as error:
            results.append({"case": case.get("case"), "status": "failed", "error": str(error)})
    counts = Counter(r["status"] for r in results)
    code_records = {"audit_script": file_record(Path(__file__))}
    repo = WORKSPACE / "work/clipforge-week4"
    for relative in ("backend/clipforge/editing/edl.py", "backend/clipforge/editing/mlt.py", "backend/clipforge/editing/subtitles.py"):
        path = repo / relative
        if path.is_file():
            code_records[relative] = file_record(path)
    report = {"status": "failed" if counts["failed"] else "pending" if counts["pending"] else "passed",
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "scope": "five-case saved Shotcut XML semantic roundtrip; no render or playback claim",
              "case_manifest": manifest_record, "source_code": code_records,
              "summary": {key: counts[key] for key in ("passed", "pending", "failed")},
              "source_media": list(source_hashes.values()), "cases": results,
              "limits": ["Pair this structural audit with actual GUI operator evidence; a file signature alone does not prove GUI actions.",
                         "This does not prove that effect controls were edited, fonts rendered correctly, audible sync, or real speech-caption alignment.",
                         "No XML byte-equivalence, stable graph-ID assumption, source copies, native encoding, network, or model calls."]}
    require(sha(args.manifest) == manifest_record["sha256"], "Case manifest changed during audit")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "summary": report["summary"], "report": str(args.output.resolve())}, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
