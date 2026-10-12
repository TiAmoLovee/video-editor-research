"""Render the saved integer-frame EDL to a verified local H.264/AAC MP4.

Video and audio are concatenated separately. Audio remains on the source time
axis until frame boundaries are converted to 48 kHz sample boundaries; late or
short audio is padded explicitly. No candidate document or score is consulted.
"""

import argparse
from bisect import bisect_left, bisect_right
from contextlib import ExitStack
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time

from clipforge.config import media_tool
from clipforge.editing.edl import _local_ref, frame_rate, validate_edl
from clipforge.editing.manifest import (
    CLEANUP_TIMEOUT, _check_identity, _identity, _pipe_reader, _probe_text, _ratio, _timeout,
    build_media_manifest, open_media_source,
)
from clipforge.editing.mlt import resolve_source_ref

AUDIO_RATE = 48000
MAX_CLIPS = 128
COPY_BYTES = 1024 * 1024
MAX_FONT_BYTES = 128 * 1024 * 1024
MAX_SUBTITLE_LOG_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


def _font_bytes(data, offset, length):
    if offset < 0 or length < 0 or offset + length > len(data):
        raise ValueError("subtitle font has a truncated OpenType table")
    return data[offset:offset + length]


def _font_number(data, offset, kind="H"):
    length = struct.calcsize(">" + kind)
    return struct.unpack(">" + kind, _font_bytes(data, offset, length))[0]


def _font_tables(data, offset):
    if _font_bytes(data, offset, 4) not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise ValueError("subtitle font requires supported OpenType outlines")
    count = _font_number(data, offset + 4)
    if not 1 <= count <= 512:
        raise ValueError("subtitle font table directory is invalid")
    tables = {}
    for index in range(count):
        entry = _font_bytes(data, offset + 12 + index * 16, 16)
        tag, _, start, length = struct.unpack(">4sIII", entry)
        if tag in tables:
            raise ValueError("subtitle font contains duplicate tables")
        tables[tag] = _font_bytes(data, start, length)
    return tables


def _font_names(table):
    count, storage = _font_number(table, 2), _font_number(table, 4)
    if count > 2048:
        raise ValueError("subtitle font name table is too large")
    names = {1: set(), 6: set(), 16: set()}
    for index in range(count):
        platform, encoding, _, name_id, length, offset = struct.unpack(
            ">6H", _font_bytes(table, 6 + index * 12, 12))
        if name_id not in names:
            continue
        value = _font_bytes(table, storage + offset, length)
        try:
            if platform in (0, 3):
                value = value.decode("utf-16-be")
            elif platform == 1 and encoding == 0:
                value = value.decode("mac_roman")
            else:
                continue
        except UnicodeError as error:
            raise ValueError("subtitle font name encoding is invalid") from error
        names[name_id].add(value)
    return names


def _font_cmap(table, glyph_count):
    count = _font_number(table, 2)
    if not 1 <= count <= 128:
        raise ValueError("subtitle font cmap directory is invalid")
    choices = []
    for index in range(count):
        platform, encoding, offset = struct.unpack(">HHI", _font_bytes(table, 4 + 8 * index, 8))
        kind = _font_number(table, offset)
        if (platform == 0 or (platform == 3 and encoding in (1, 10))) and kind in (4, 12):
            choices.append((kind == 12, platform == 3 and encoding == 10, offset, kind))
    if not choices:
        raise ValueError("subtitle font has no supported Unicode cmap")
    _, _, offset, kind = max(choices)
    length = _font_number(table, offset + (4 if kind == 12 else 2), "I" if kind == 12 else "H")
    mapping = _font_bytes(table, offset, length)
    if kind == 12:
        groups = _font_number(mapping, 12, "I")
        if groups > (len(mapping) - 16) // 12:
            raise ValueError("subtitle font cmap groups are truncated")
        ranges = [struct.unpack(">III", _font_bytes(mapping, 16 + 12 * index, 12))
                  for index in range(groups)]
        previous = -1
        for start, end, first in ranges:
            if not previous < start <= end <= 0x10FFFF or first + end - start >= glyph_count:
                raise ValueError("subtitle font cmap groups are invalid")
            previous = end
        starts = [item[0] for item in ranges]

        def lookup(codepoint):
            index = bisect_right(starts, codepoint) - 1
            if index < 0 or codepoint > ranges[index][1]:
                return 0
            return ranges[index][2] + codepoint - ranges[index][0]
    else:
        doubled = _font_number(mapping, 6)
        if doubled == 0 or doubled % 2:
            raise ValueError("subtitle font cmap segments are invalid")
        count = doubled // 2
        ends = [_font_number(mapping, 14 + 2 * index) for index in range(count)]
        if ends != sorted(set(ends)):
            raise ValueError("subtitle font cmap segments are unordered")

        def lookup(codepoint):
            if codepoint > 0xFFFF:
                return 0
            index = bisect_left(ends, codepoint)
            if index == count:
                return 0
            start = _font_number(mapping, 16 + 2 * count + 2 * index)
            if codepoint < start:
                return 0
            delta = _font_number(mapping, 16 + 4 * count + 2 * index)
            position = 16 + 6 * count + 2 * index
            distance = _font_number(mapping, position)
            if distance:
                glyph = _font_number(mapping, position + distance + 2 * (codepoint - start))
                glyph = (glyph + delta) % 65536 if glyph else 0
            else:
                glyph = (codepoint + delta) % 65536
            if glyph >= glyph_count:
                raise ValueError("subtitle font cmap glyph index is invalid")
            return glyph
    return lookup


def _ass_characters(ass):
    text = []
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 9)
            if len(fields) != 10:
                raise ValueError("generated ASS dialogue is invalid")
            value = re.sub(r"(?<!\\)\{[^}]*\}", "", fields[9])
            for escaped, character in ((r"\N", "\n"), (r"\n", "\n"), (r"\h", " "),
                                       (r"\{", "{"), (r"\}", "}"), (r"\\", "\\")):
                value = value.replace(escaped, character)
            text.append(value)
    return {ord(character) for character in "".join(text) if not character.isspace()}


def _font_info(data, family, characters):
    if data[:4] == b"ttcf":
        count = _font_number(data, 8, "I")
        if not 1 <= count <= 64:
            raise ValueError("subtitle font collection has too many faces")
        offsets = [_font_number(data, 12 + 4 * index, "I") for index in range(count)]
    else:
        offsets = [0]
    faces, postscript = [], set()
    for index, offset in enumerate(offsets):
        tables = _font_tables(data, offset)
        if not {b"name", b"cmap", b"maxp"} <= tables.keys():
            raise ValueError("subtitle font lacks required OpenType tables")
        names = _font_names(tables[b"name"])
        if family.casefold() not in {name.casefold() for name in names[1] | names[16]}:
            continue
        if not names[6] or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,127}", name) for name in names[6]):
            raise ValueError("subtitle font PostScript identity is unsupported")
        lookup = _font_cmap(tables[b"cmap"], _font_number(tables[b"maxp"], 4))
        missing = sorted(codepoint for codepoint in characters if lookup(codepoint) == 0)
        if missing:
            raise ValueError("configured subtitle font lacks glyph " + ", ".join(f"U+{item:04X}" for item in missing[:8]))
        faces.append(index)
        postscript.update(names[6])
    if not faces:
        raise ValueError("SUBTITLE_FONT_NAME does not identify a family in SUBTITLE_FONT_FILE")
    return {"face_indices": faces, "postscript_names": sorted(postscript), "glyph_count": len(characters)}


def _subtitle_tool(command, timeout, *, cwd=None):
    """Retain bounded libass diagnostics using raw pipes, without reader threads."""
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=0, cwd=cwd)
    outputs = [bytearray(), bytearray()]
    streams = [process.stdout, process.stderr]
    ended = [False, False]
    try:
        available = _pipe_reader(process)
        while True:
            _remaining(deadline)
            active = False
            for index, stream in enumerate(streams):
                if ended[index]:
                    continue
                chunk = available(stream)
                if chunk is not None:
                    active = True
                    ended[index] = not chunk
                    outputs[index].extend(chunk)
                    if len(outputs[index]) > MAX_SUBTITLE_LOG_BYTES:
                        raise RuntimeError("subtitle tool diagnostics exceed the bounded limit")
            if all(ended) and process.poll() is not None:
                if process.returncode:
                    # Keep detailed tool diagnostics in backend logs only. The
                    # caller exposes a stable safe error to HTTP consumers.
                    diagnostic = bytes(outputs[1][-8192:]).decode("utf-8", errors="replace")
                    logger.error("FFmpeg subtitle tool exited with status %s; stderr tail: %s",
                                 process.returncode, diagnostic)
                    raise RuntimeError("FFmpeg subtitle rendering failed")
                return tuple(bytes(value).decode("utf-8", errors="replace") for value in outputs)
            if not active:
                time.sleep(min(0.005, _remaining(deadline)))
    finally:
        try:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=CLEANUP_TIMEOUT)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("subtitle tool did not terminate within its bounded cleanup grace") from error
        finally:
            process.stdout.close()
            process.stderr.close()


def _subtitle_state(stack, plan, edl, analyses, font_file, font_name, executable, deadline):
    from clipforge.editing.subtitles import to_ass, validate_subtitles

    plan = deepcopy(plan)
    validate_subtitles(plan, edl, analyses)
    configured = font_file if font_file is not None else os.environ.get("SUBTITLE_FONT_FILE")
    family = font_name if font_name is not None else os.environ.get("SUBTITLE_FONT_NAME")
    if not configured or not isinstance(family, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}", family):
        raise ValueError("subtitle burn-in requires SUBTITLE_FONT_FILE and a safe SUBTITLE_FONT_NAME")
    if family != family.strip():
        raise ValueError("subtitle font family must not have surrounding whitespace")
    font = Path(configured).expanduser()
    _local_ref(str(font))
    incoming = stack.enter_context(open_media_source(font))
    identity = _identity(os.fstat(incoming.fileno()))
    blocks, size = [], 0
    while chunk := incoming.read(COPY_BYTES):
        _remaining(deadline)
        size += len(chunk)
        if size > MAX_FONT_BYTES:
            raise ValueError("subtitle font exceeds the bounded file limit")
        blocks.append(chunk)
    data = b"".join(blocks)
    ass = to_ass(plan, font_name=family)
    if not isinstance(ass, str) or len(ass.encode("utf-8")) > MAX_SUBTITLE_LOG_BYTES:
        raise ValueError("generated ASS is not bounded UTF-8 text")
    info = _font_info(data, family, _ass_characters(ass))
    filters, _ = _subtitle_tool([executable, "-hide_banner", "-loglevel", "error", "-filters"], _tool_budget(deadline))
    if not re.search(r"(?m)^\s*[A-Z.]{3}\s+ass\s+V->V(?:\s|$)", filters):
        raise RuntimeError("configured FFmpeg lacks the libass ASS filter")
    _check_identity(font, identity)
    return {"data": data, "ass": ass, "path": font, "handle": incoming, "identity": identity,
            "filename": "font.ttc" if data[:4] == b"ttcf" else "font.otf" if data[:4] == b"OTTO" else "font.ttf",
            "report": {"plan_sha256": _json_digest(plan), "ass_sha256": hashlib.sha256(ass.encode("utf-8")).hexdigest(),
                       "font_sha256": hashlib.sha256(data).hexdigest(), "font_name": family, **info}}


def _validate_subtitle_logs(log, state):
    if re.search(r"\[(?:error|fatal|panic)\]|glyph.{0,80}(?:not found|missing)|failed to find.*(?:font|fallback)"
                 r"|ass_read_file.{0,200}(?:failed|error)|error opening (?:memory )?font",
                 log, re.IGNORECASE):
        raise RuntimeError("libass reported a missing glyph, font fallback failure or rendering error")
    if "libass" not in log.lower() or not re.search(r"Loading font file.*" + re.escape(state["filename"]), log):
        raise RuntimeError("libass did not confirm loading the configured font snapshot")
    selections = re.findall(r"fontselect:.*? -> (.*),\s*(\d+),\s*([^,\r\n]+)", log)
    if not selections or any(name.strip() not in state["report"]["postscript_names"]
                             or int(index) not in state["report"]["face_indices"]
                             for _, index, name in selections):
        raise RuntimeError("libass selected a font outside the configured font file/family")


def _remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise RuntimeError("EDL render timed out")
    return value


def _tool_budget(deadline):
    # The shared budget includes the bounded direct-process cleanup grace.
    value = _remaining(deadline) - CLEANUP_TIMEOUT
    if value <= 0:
        raise RuntimeError("EDL render timed out")
    return value


def _manifest_budget(deadline):
    value = int(_tool_budget(deadline))
    if value < 1:
        raise RuntimeError("EDL render has insufficient remaining verification budget")
    return value


def _json_digest(value):
    content = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _hash_handle(incoming, deadline):
    incoming.seek(0)
    hashed = hashlib.sha256()
    while True:
        _remaining(deadline)
        chunk = incoming.read(COPY_BYTES)
        if not chunk:
            break
        hashed.update(chunk)
    _remaining(deadline)
    incoming.seek(0)
    return hashed.hexdigest()


def _check_source(record, deadline, *, hash_content=False):
    _remaining(deadline)
    _check_identity(record["path"], record["identity"])
    if _identity(os.fstat(record["handle"].fileno())) != record["identity"]:
        raise ValueError("source file changed during rendering")
    if hash_content and _hash_handle(record["handle"], deadline) != record["sha256"]:
        raise ValueError("locked source sha256 differs from the EDL")
    _check_identity(record["path"], record["identity"])


def _sources(edl, source_root, stack, executable, deadline):
    records, by_id = {}, {}
    for source in edl["sources"]:
        path = Path(resolve_source_ref(source["ref"], source_root, require_media=True))
        # Windows normcase also deduplicates references with different casing.
        key = os.path.normcase(str(path))
        if key not in records:
            incoming = stack.enter_context(open_media_source(path))
            record = {"path": path, "handle": incoming, "identity": _identity(os.fstat(incoming.fileno())),
                      "sha256": source["sha256"], "source_ids": []}
            _check_source(record, deadline, hash_content=True)
            measured = build_media_manifest(path, source_id=source["id"], ref=source["ref"],
                                            ffprobe=executable, timeout=_manifest_budget(deadline),
                                            expected_fps=source["fps"])
            record["measured"] = measured
            records[key] = record
        record = records[key]
        actual = deepcopy(record["measured"]["source"])
        actual.update(id=source["id"], ref=source["ref"])
        expected_profile = ({key: source[key] for key in ("width", "height", "fps")}
                            if edl["schema_version"] == "0.2.0" else edl["profile"])
        if actual != source or record["measured"]["profile"] != expected_profile:
            raise ValueError(f"measured source descriptor differs from EDL: {source['id']}")
        _check_source(record, deadline)
        record["source_ids"].append(source["id"])
        by_id[source["id"]] = record
    return list(records.values()), by_id


def _sample(frame, fps):
    position = Fraction(frame * AUDIO_RATE, 1) / frame_rate(fps)
    return (2 * position.numerator + position.denominator) // (2 * position.denominator)


def _filter_graph(edl):
    fps = edl["profile"]["fps"]
    sources = {source["id"]: source for source in edl["sources"]}
    portrait = edl["schema_version"] == "0.2.0"
    crops = {crop["source_id"]: crop for crop in edl["framing"]["sources"]} if portrait else {}
    has_audio = any(sources[clip["source_id"]]["audio_stream_index"] is not None for clip in edl["clips"])
    graph, decisions = [], []
    for index, clip in enumerate(edl["clips"]):
        source = sources[clip["source_id"]]
        start, end = clip["source_in_frame"], clip["source_out_frame"]
        geometry = "scale=w=iw:h=ih:"
        if portrait:
            crop = crops[clip["source_id"]]
            geometry = (f"crop=w={crop['width']}:h={crop['height']}:x={crop['x']}:y={crop['y']},"
                        f"scale=w={edl['profile']['width']}:h={edl['profile']['height']}:")
        graph.append(f"[{index}:{source['video_stream_index']}]trim=start_frame={start}:end_frame={end},"
                     f"settb=expr={fps['den']}/{fps['num']},setpts=N,setsar=1,"
                     f"{geometry}in_range=auto:out_range=tv:in_color_matrix=bt709:out_color_matrix=bt709,"
                     f"format=yuv420p[v{index}]")
        decision = {key: clip[key] for key in ("id", "source_id", "source_in_frame", "source_out_frame",
                                             "timeline_in_frame", "timeline_out_frame")}
        if has_audio:
            first, last = _sample(start, fps), _sample(end, fps)
            samples = _sample(clip["timeline_out_frame"], fps) - _sample(clip["timeline_in_frame"], fps)
            if source["audio_stream_index"] is None:
                audio = "anullsrc=r=48000:cl=stereo,"
            else:
                audio = (f"[{index}:{source['audio_stream_index']}]aresample=48000:async=1:first_pts=0,"
                         "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                         f"atrim=start_sample={first}:end_sample={last},")
            graph.append(audio + "asetpts=N/SR/TB," + f"apad=whole_len={samples},atrim=end_sample={samples},"
                         f"asetpts=N/SR/TB[a{index}]")
            decision.update(source_audio_start_sample=first, source_audio_end_sample=last,
                            timeline_audio_samples=samples, audio_silence=source["audio_stream_index"] is None)
        decisions.append(decision)
    count = len(edl["clips"])
    graph.append("".join(f"[v{index}]" for index in range(count)) + f"concat=n={count}:v=1:a=0,"
                 f"settb=expr={fps['den']}/{fps['num']},setpts=N[vout]")
    if has_audio:
        samples = _sample(edl["duration_frames"], fps)
        graph.append("".join(f"[a{index}]" for index in range(count)) + f"concat=n={count}:v=0:a=1,"
                     f"atrim=end_sample={samples},asetpts=N/SR/TB[aout]")
    return ";\n".join(graph) + "\n", has_audio, decisions


def _command(edl, by_id, executable, graph_path, temporary, has_audio, *, video_label="vout"):
    command = [executable, "-hide_banner", "-loglevel", "error", "-nostats", "-nostdin", "-n", "-copyts"]
    # Separate input instances avoid split-filter buffering when clips revisit
    # one source in a different order. The physical source is still locked and
    # measured only once; every trim remains in that source's full frame axis.
    for clip in edl["clips"]:
        command += ["-noautorotate", "-i", str(by_id[clip["source_id"]]["path"])]
    command += ["-filter_complex_script", str(graph_path), "-map", f"[{video_label}]",
                "-map_metadata", "-1", "-map_chapters", "-1", "-fps_mode", "cfr",
                "-enc_time_base:v", f"{edl['profile']['fps']['den']}/{edl['profile']['fps']['num']}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-bf", "0", "-pix_fmt", "yuv420p",
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    if has_audio:
        command += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        command += ["-an"]
    command += ["-metadata", "comment=ClipForge EDL renderer 0.1.0", "-metadata", "editing=ClipForge",
                "-movflags", "+faststart+use_metadata_tags", str(temporary)]
    if os.name == "nt" and len(subprocess.list2cmdline(command)) >= 32760:
        raise ValueError("render command exceeds the Windows command-line limit")
    return command


def _output_facts(video, executable, deadline, expected_audio, expected_samples):
    fields = ("stream=index,codec_type,codec_name,pix_fmt,sample_rate,channels,start_pts,duration_ts,time_base:"
              "format=format_name:format_tags=comment,editing")
    raw = json.loads(_probe_text([executable, "-v", "error", "-show_entries", fields,
                                 "-of", "json", str(video)], _tool_budget(deadline)))
    if (not isinstance(raw, dict) or not isinstance(raw.get("streams"), list)
            or any(not isinstance(item, dict) for item in raw["streams"])):
        raise ValueError("rendered stream metadata is malformed")
    container = raw.get("format", {})
    if (not isinstance(container, dict) or not isinstance(container.get("format_name"), str)
            or "mp4" not in container["format_name"].split(",")):
        raise ValueError("rendered output is not a measured MP4 container")
    streams = raw["streams"]
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if len(videos) != 1 or videos[0].get("codec_name") != "h264" or videos[0].get("pix_fmt") != "yuv420p":
        raise ValueError("rendered output requires exactly one H.264 yuv420p video stream")
    if len(streams) != 1 + int(expected_audio) or len(audios) != int(expected_audio):
        raise ValueError("rendered output audio presence or unexpected streams differ from the EDL")
    if expected_audio and (audios[0].get("codec_name") != "aac" or audios[0].get("sample_rate") != "48000"
                           or audios[0].get("channels") != 2):
        raise ValueError("rendered audio requires AAC at 48000 Hz stereo")
    audio_samples = None
    if expected_audio:
        audio = audios[0]
        if type(audio.get("start_pts")) is not int or audio["start_pts"] != 0:
            raise ValueError("rendered AAC audio must start at measured PTS zero")
        if type(audio.get("duration_ts")) is not int or audio["duration_ts"] <= 0:
            raise ValueError("rendered AAC track duration requires measured integer ticks")
        audio_samples = audio["duration_ts"] * _ratio(audio.get("time_base"), "rendered AAC time_base") * AUDIO_RATE
        # AAC packets contain 1024 samples. MP4 priming/padding metadata can
        # quantize the encoded track duration; pre-encode decisions remain exact.
        if abs(audio_samples - expected_samples) > 1024:
            raise ValueError("rendered AAC duration differs by more than one codec packet")
    tags = container.get("tags", {})
    if (not isinstance(tags, dict) or tags.get("comment") != "ClipForge EDL renderer 0.1.0"
            or tags.get("editing") != "ClipForge"):
        raise ValueError("rendered MP4 lacks the ClipForge tool metadata")
    return {"video_codec": "h264", "pixel_format": "yuv420p",
            "audio_codec": "aac" if expected_audio else None,
            "audio_sample_rate": AUDIO_RATE if expected_audio else None,
            "audio_channels": 2 if expected_audio else None,
            "audio_track_samples": float(audio_samples) if audio_samples is not None else None,
            "tool": "ClipForge"}


def _publish(incoming, target, expected_sha256, deadline):
    incoming.seek(0)
    created = None
    try:
        with target.open("xb") as outgoing:
            created = os.fstat(outgoing.fileno())
            hashed = hashlib.sha256()
            while True:
                _remaining(deadline)
                chunk = incoming.read(COPY_BYTES)
                if not chunk:
                    break
                outgoing.write(chunk)
                hashed.update(chunk)
            if hashed.hexdigest() != expected_sha256:
                raise ValueError("verified temporary media changed during publication")
            outgoing.flush()
            os.fsync(outgoing.fileno())
            _remaining(deadline)
        with open_media_source(target) as published:
            final_identity = _identity(os.fstat(published.fileno()))
            if final_identity[:2] != (created.st_dev, created.st_ino):
                raise ValueError("render output was replaced during publication")
            if _hash_handle(published, deadline) != expected_sha256:
                raise ValueError("published render sha256 differs from verified media")
            if _identity(os.fstat(published.fileno())) != final_identity:
                raise ValueError("published render changed during final verification")
            _check_identity(target, final_identity)
    except BaseException:
        if created is not None:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    target.unlink()
            except FileNotFoundError:
                pass
        raise


def render_edl(edl, source_root, output, *, ffmpeg=None, ffprobe=None, timeout=3600,
               subtitle_plan=None, analyses=None, font_file=None, font_name=None) -> dict:
    """Render new output exclusively; return measured facts and planned frame cuts.

Sources must pass the measured manifest contract at the project frame rate.
Version 0.1 preserves native dimensions; version 0.2 applies the saved native
crop before scaling to the output profile. At most 128 clips are accepted. AAC encoding
has codec priming/padding; sample decisions describe the pre-encode audio axis.
Output frame PTS/count are measured, while content/order needs an independent
frame-marker check (the report does not claim to infer content identity).
Optional subtitles require a validated plan, source analyses, and an explicitly
configured font file/family. Glyph coverage and libass's actual selections are
verified; no system-font fallback is accepted.
"""
    _timeout(timeout)
    started = time.monotonic()
    deadline = started + timeout
    data = deepcopy(edl)
    validate_edl(data)
    if len(data["clips"]) > MAX_CLIPS:
        raise ValueError(f"renderer supports at most {MAX_CLIPS} clips")
    if data["profile"]["width"] % 2 or data["profile"]["height"] % 2:
        raise ValueError("H.264 yuv420p rendering requires even source/profile dimensions")
    target = Path(output).expanduser()
    _local_ref(str(target))
    if target.exists() or target.is_symlink():
        raise FileExistsError("render output already exists; use a new filename")
    if target.suffix.lower() != ".mp4":
        raise ValueError("render output must use the .mp4 extension")
    target = target.resolve()
    ffmpeg, ffprobe = media_tool("ffmpeg", ffmpeg), media_tool("ffprobe", ffprobe)
    if subtitle_plan is not None:
        # The subtitle subprocess has a private cwd. Freeze executable lookup
        # before entering it so explicit relative tool paths keep their meaning.
        ffmpeg = shutil.which(ffmpeg) or str(Path(ffmpeg).resolve())
    graph, has_audio, decisions = _filter_graph(data)
    with ExitStack() as stack:
        subtitles = None
        if subtitle_plan is not None:
            subtitles = _subtitle_state(stack, subtitle_plan, data, analyses, font_file, font_name, ffmpeg, deadline)
        records, by_id = _sources(data, source_root, stack, ffprobe, deadline)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Older Windows FFmpeg builds cannot chdir to or open filter scripts
        # beyond MAX_PATH. Subtitle mode uses the OS temp directory and fixed
        # relative tool paths; publication still copies to the final held file.
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix=".clipforge-render-", dir=None if subtitles is not None else target.parent)))
        temporary, graph_path = folder / "rendered.mp4", folder / "filters.txt"
        label = "vout"
        if subtitles is not None:
            ass_path = folder / "subtitles.ass"
            ass_path.write_bytes(subtitles["ass"].encode("utf-8"))
            ass_handle = stack.enter_context(open_media_source(ass_path))
            if _hash_handle(ass_handle, deadline) != subtitles["report"]["ass_sha256"]:
                raise ValueError("temporary ASS differs from the validated subtitle plan")
            fonts = folder / "fonts"
            fonts.mkdir()
            copied = fonts / subtitles["filename"]
            with copied.open("xb") as outgoing:
                outgoing.write(subtitles["data"])
            copied_handle = stack.enter_context(open_media_source(copied))
            if _hash_handle(copied_handle, deadline) != subtitles["report"]["font_sha256"]:
                raise ValueError("temporary subtitle font differs from its held source")
            # Fixed relative names in a private cwd avoid interpolating paths
            # containing drive colons, quotes, commas, or filtergraph brackets.
            graph = graph.rstrip() + ";\n[vout]ass=filename=subtitles.ass:fontsdir=fonts[subout]\n"
            label = "subout"
        graph_path.write_text(graph, encoding="utf-8")
        command = _command(data, by_id, ffmpeg, graph_path, temporary, has_audio, video_label=label)
        try:
            if subtitles is None:
                _probe_text(command, _tool_budget(deadline))
            else:
                command[command.index("-loglevel") + 1] = "level+verbose"
                command[command.index("-filter_complex_script") + 1] = graph_path.name
                command[-1] = temporary.name
                _, diagnostics = _subtitle_tool(command, _tool_budget(deadline), cwd=folder)
                _validate_subtitle_logs(diagnostics, subtitles)
                if _hash_handle(subtitles["handle"], deadline) != subtitles["report"]["font_sha256"]:
                    raise ValueError("subtitle font changed during rendering")
                _check_identity(subtitles["path"], subtitles["identity"])
                if _hash_handle(copied_handle, deadline) != subtitles["report"]["font_sha256"]:
                    raise ValueError("subtitle font snapshot changed during rendering")
                if _hash_handle(ass_handle, deadline) != subtitles["report"]["ass_sha256"]:
                    raise ValueError("ASS snapshot changed during rendering")
        except RuntimeError as error:
            raise RuntimeError(f"FFmpeg EDL render failed: {error}") from error
        for record in records:
            _check_source(record, deadline, hash_content=True)
        with open_media_source(temporary) as verified:
            measured = build_media_manifest(temporary, source_id="rendered", ref=target.as_posix(),
                                            ffprobe=ffprobe, timeout=_manifest_budget(deadline),
                                            expected_fps=data["profile"]["fps"])
            source = measured["source"]
            if measured["profile"] != data["profile"] or source["total_frames"] != data["duration_frames"]:
                raise ValueError("rendered output frame count/profile differs from the saved EDL")
            if (source["audio_stream_index"] is not None) != has_audio:
                raise ValueError("rendered output audio presence differs from selected clips")
            facts = _output_facts(temporary, ffprobe, deadline, has_audio,
                                  _sample(data["duration_frames"], data["profile"]["fps"]))
            _publish(verified, target, source["sha256"], deadline)
    report = {"schema_version": "0.1.0", "artifact_kind": "edl_render_verification",
            "edl_sha256": _json_digest(data), "elapsed_seconds": round(time.monotonic() - started, 3),
            "output": {"path": target.as_posix(), "sha256": source["sha256"],
                       "profile": measured["profile"], "total_frames": source["total_frames"],
                       "audio_stream_index": source["audio_stream_index"], **facts},
            "sources": [{"source_ids": record["source_ids"], "sha256": record["sha256"],
                         "total_frames": record["measured"]["source"]["total_frames"]} for record in records],
            "clips": decisions,
            "audio": {"policy": "source-timeline-resample-trim-pad" if has_audio else "absent",
                      "sample_rate": AUDIO_RATE if has_audio else None,
                      "pre_encode_total_samples": _sample(data["duration_frames"], data["profile"]["fps"]) if has_audio else 0},
            "checks": {"source_descriptors_match": True, "source_sha256_match": True,
                       "output_cfr_pts_match": True, "output_frame_count_match": True,
                       "output_media_profile_match": True, "output_audio_presence_match": True,
                       "output_sha256_match": True, "output_tool_metadata_match": True}}
    if subtitles is not None:
        report["subtitles"] = subtitles["report"]
        report["checks"].update(subtitle_plan_valid=True, subtitle_font_glyphs_present=True,
                                subtitle_font_selected=True, subtitles_burned=True)
    if data["schema_version"] == "0.2.0":
        report["framing"] = deepcopy(data["framing"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("edl", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--ffprobe", default=None)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    try:
        data = json.loads(args.edl.read_text(encoding="utf-8-sig"))
        report = render_edl(data, args.source_root, args.output, ffmpeg=args.ffmpeg,
                            ffprobe=args.ffprobe, timeout=args.timeout)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"INVALID: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
