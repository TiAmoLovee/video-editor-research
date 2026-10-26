"""Measure a local video's EDL media profile without modifying or transcoding it.

Production defaults to 30/1 fps. Average frame rate is only a preliminary check;
every decoded frame must have measured integer PTS and matching frame properties.
Only the profile/source contract is returned, with no inferred color or SAR flags.
"""

import argparse
from collections import deque
from contextlib import closing, contextmanager
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from clipforge.config import media_tool
from clipforge.media.probe import normalize_metadata
from clipforge.media.split import read_frame_count
from clipforge.editing.edl import (
    MAX_FRAMES, _load_schema, _local_ref, _profile_sources, _schema_validate, frame_rate,
)

MAX_LINE_BYTES = 16384
MAX_METADATA_BYTES = 4 * 1024 * 1024
CLEANUP_TIMEOUT = 0.2
DEFAULT_FPS = {"num": 30, "den": 1}
COLOR_FIELDS = ("color_space", "color_transfer", "color_primaries")


def _timeout(value):
    if type(value) is not int or not 1 <= value <= 86400:
        raise ValueError("timeout must be an integer from 1 to 86400 seconds")
    return value


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("FFprobe verification timed out")
    return remaining


def _pipe_reader(process):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                         ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        peek.restype = wintypes.BOOL

        def read_available(stream):
            available = wintypes.DWORD()
            if not peek(msvcrt.get_osfhandle(stream.fileno()), None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error == 109:  # ERROR_BROKEN_PIPE: every writer has closed.
                    return b""
                raise ctypes.WinError(error)
            if not available.value:
                return None
            return os.read(stream.fileno(), min(available.value, 8192))
    else:
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)

        def read_available(stream):
            try:
                return os.read(stream.fileno(), 8192)
            except BlockingIOError:
                return None

    return read_available


def _probe_lines(command, timeout):
    """Poll bounded raw pipes on one deadline, including inherited open pipes.

The configured process is killed on failure; user-supplied wrapper descendants
are not process-tree managed. Raw pipes are always closed in this thread, so an
inherited writer cannot block cleanup or retain our pipe handles. Killing the
direct process permits up to 0.2 seconds beyond the verification deadline to reap
its asynchronous termination; no grace is needed for an already exited wrapper.
"""
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0)
    except FileNotFoundError as error:
        raise RuntimeError("FFprobe executable was not found") from error
    errors = deque(maxlen=8)
    pending = bytearray()
    stdout_done, stderr_done = False, False

    def diagnostic():
        detail = b"".join(errors).decode("utf-8", errors="replace").strip()
        return re.sub(r"[\x00-\x1f\x7f]", " ", detail)[:1024] or "no diagnostic"

    try:
        read_available = _pipe_reader(process)
        while True:
            _remaining(deadline)
            active = False
            if not stderr_done:
                chunk = read_available(process.stderr)
                if chunk is not None:
                    active = True
                    stderr_done = not chunk
                    if chunk:
                        # Fixed-size pieces keep the entire retained tail <=8 KiB.
                        errors.extend(chunk[position:position + 1024] for position in range(0, len(chunk), 1024))
                        if chunk.strip():
                            raise RuntimeError(f"FFprobe verification failed: {diagnostic()}")
            if not stdout_done:
                chunk = read_available(process.stdout)
                if chunk is not None:
                    active = True
                    stdout_done = not chunk
                    pending.extend(chunk)
                    while b"\n" in pending:
                        boundary = pending.index(b"\n") + 1
                        if boundary > MAX_LINE_BYTES:
                            raise ValueError("FFprobe output line exceeds the bounded parser limit")
                        line = bytes(pending[:boundary])
                        del pending[:boundary]
                        yield line.decode("utf-8", errors="strict")
                    if len(pending) > MAX_LINE_BYTES:
                        raise ValueError("FFprobe output line exceeds the bounded parser limit")
            if stdout_done and stderr_done and process.poll() is not None:
                if process.returncode != 0:
                    raise RuntimeError(f"FFprobe verification failed (exit {process.returncode}): {diagnostic()}")
                if pending:
                    yield pending.decode("utf-8", errors="strict")
                break
            if not active:
                time.sleep(min(0.005, _remaining(deadline)))
    finally:
        try:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=CLEANUP_TIMEOUT)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError("FFprobe did not terminate within its bounded cleanup grace") from error
        finally:
            process.stdout.close()
            process.stderr.close()


def _probe_text(command, timeout):
    chunks, length = [], 0
    with closing(_probe_lines(command, timeout)) as lines:
        for line in lines:
            length += len(line.encode("utf-8"))
            if length > MAX_METADATA_BYTES:
                raise ValueError("FFprobe stream metadata exceeds the bounded parser limit")
            chunks.append(line)
    return "".join(chunks)


def _probe_details(video, executable, timeout):
    fields = (
        "format=duration,format_name:"
        "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,"
        "time_base,field_order,sample_aspect_ratio,color_space,color_transfer,color_primaries,"
        "sample_rate,channels:stream_tags=rotate:stream_side_data=rotation,side_data_type:"
        "stream_disposition=attached_pic"
    )
    command = [executable, "-v", "error", "-show_entries", fields, "-of", "json", str(video)]
    value = json.loads(_probe_text(command, timeout))
    if not isinstance(value, dict) or not isinstance(value.get("streams"), list):
        raise ValueError("FFprobe returned invalid stream metadata")
    return value


def _integer(value, name, *, maximum=MAX_FRAMES):
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        number = int(value)
    else:
        raise ValueError(f"{name} requires a measured integer")
    if not 0 <= number <= maximum:
        raise ValueError(f"{name} is out of range")
    return number


def _ratio(value, name, separator="/"):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*" + re.escape(separator) + r"[1-9][0-9]*", value):
        raise ValueError(f"{name} is missing or unknown")
    numerator, denominator = map(int, value.split(separator))
    return Fraction(numerator, denominator)


def _frame_properties(value, width, height, *, frame=False):
    if (not _integer(value.get("width"), "width", maximum=16384) == width
            or not _integer(value.get("height"), "height", maximum=16384) == height):
        raise ValueError("decoded frame dimensions differ from the project profile")
    if _ratio(value.get("sample_aspect_ratio"), "sample_aspect_ratio", ":") != 1:
        raise ValueError("only measured square-pixel SAR is supported")
    if any(value.get(field) != "bt709" for field in COLOR_FIELDS):
        raise ValueError("measured BT.709 space, transfer and primaries are required")
    if frame:
        if _integer(value.get("interlaced_frame"), "interlaced_frame") != 0:
            raise ValueError("interlaced decoded frames are unsupported")
    elif value.get("field_order") != "progressive":
        raise ValueError("measured progressive field_order is required")


def _rotation(stream):
    rotations = []
    tags = stream.get("tags", {})
    if not isinstance(tags, dict):
        raise ValueError("invalid rotation tags")
    if "rotate" in tags:
        rotations.append(tags["rotate"])
    side_data = stream.get("side_data_list", [])
    if not isinstance(side_data, list):
        raise ValueError("invalid rotation side data")
    for entry in side_data:
        if not isinstance(entry, dict):
            raise ValueError("invalid rotation side data")
        if "displaymatrix" in str(entry.get("side_data_type", "")).lower().replace(" ", "") and "rotation" not in entry:
            raise ValueError("display-matrix rotation is unknown")
        if "rotation" in entry:
            rotations.append(entry["rotation"])
    for rotation in rotations:
        if isinstance(rotation, bool) or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", str(rotation)):
            raise ValueError("rotation is unknown")
        if Fraction(str(rotation)) != 0:
            raise ValueError("rotated sources are unsupported")
    # With neither rotate tag nor display-matrix orientation, the measured
    # container's orientation is the format default of zero, not an unknown tag.
    return 0


def _frame_rotation(values):
    # Recent compact FFprobe uses side_datum/<type>:rotation; older releases
    # use unprefixed rotation. Both represent decoded-frame orientation.
    rotations = [value for key, value in values.items() if key == "rotation" or key.endswith(":rotation")]
    matrices = [key for key, value in values.items()
                if (key == "side_data_type" or key.endswith(":side_data_type"))
                and "displaymatrix" in value.lower().replace(" ", "")]
    for key in matrices:
        rotation_key = key.removesuffix("side_data_type") + "rotation"
        if rotation_key not in values:
            raise ValueError("decoded display-matrix rotation is unknown")
    _rotation({"side_data_list": [{"rotation": value} for value in rotations]})


def _scan_frames(video, executable, stream, fps, deadline):
    index = stream["index"]
    tick = _ratio(stream.get("time_base"), "time_base")
    spacing = 1 / (frame_rate(fps) * tick)
    # Integral positions require no timestamp quantization. For fractional
    # positions allow <=1 tick, but reject clocks too coarse to detect a lost frame.
    tolerance = 0 if spacing.denominator == 1 else 1
    if spacing < 1 or (tolerance and spacing < 2):
        raise ValueError("time_base is too coarse to prove contiguous CFR frames")
    fields = ("frame=stream_index,pts,interlaced_frame,width,height,sample_aspect_ratio,color_space,color_transfer,color_primaries:"
              "frame_side_data=rotation,side_data_type")
    command = [executable, "-v", "error", "-select_streams", str(index), "-show_frames",
               "-show_entries", fields, "-of", "compact=p=1:nk=0", str(video)]
    count, previous = 0, None
    with closing(_probe_lines(command, _remaining(deadline))) as lines:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("frame|"):
                raise ValueError("FFprobe emitted an unexpected frame record")
            values = {}
            for item in line.split("|")[1:]:
                if "=" in item:
                    key, value = item.split("=", 1)
                    if key in values:
                        raise ValueError("FFprobe emitted duplicate frame fields")
                    values[key] = value
            if _integer(values.get("stream_index"), "stream_index") != index:
                raise ValueError("FFprobe decoded an unexpected video stream")
            _frame_properties(values, stream["width"], stream["height"], frame=True)
            _frame_rotation(values)
            pts = _integer(values.get("pts"), "pts", maximum=2**63 - 1)
            if count == 0 and pts != 0:
                raise ValueError("normalized source must begin at measured PTS zero")
            if previous is not None and (pts <= previous or abs((pts - previous) - spacing) > tolerance):
                raise ValueError("missing, repeated or noncontiguous frame timestamps (VFR)")
            if abs(Fraction(pts) - count * spacing) > tolerance:
                raise ValueError("frame timestamps do not match the expected CFR grid (VFR)")
            previous = pts
            count += 1
            if count > MAX_FRAMES:
                raise ValueError("decoded frame count exceeds the EDL/MLT limit")
    if not count:
        raise ValueError("source has no decoded video frames")
    return count


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _check_identity(video, initial):
    current = video.lstat()
    if not stat.S_ISREG(current.st_mode) or _identity(current) != initial:
        raise ValueError("source file changed during media verification")


@contextmanager
def open_media_source(video: Path):
    """Hold one read handle throughout measurement and hash that same handle.

Windows sharing denies writes/deletion, including write-and-restore races that
mtime and Windows creation-time based ctime cannot detect. POSIX flock is
cooperative; ordinary noncooperative writes are caught by mtime/ctime checks.
"""
    video = Path(video)
    _local_ref(str(video))
    if not stat.S_ISREG(video.lstat().st_mode):
        raise ValueError("source must be an ordinary local file, not a symlink or directory")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(video), 0x80000000, 1, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, "rb") as incoming:
            yield incoming
    else:
        import fcntl

        with video.open("rb") as incoming:
            fcntl.flock(incoming.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            try:
                yield incoming
            finally:
                fcntl.flock(incoming.fileno(), fcntl.LOCK_UN)


def build_media_manifest(video: Path, *, source_id="normalized", ref=None, ffprobe=None,
                         timeout=3600, expected_fps=None, _single_decode=False) -> dict:
    """Return exactly profile/source from measured, unchanged local media."""
    _timeout(timeout)
    if type(_single_decode) is not bool:
        raise ValueError("single-decode verification policy must be boolean")
    fps = deepcopy(DEFAULT_FPS if expected_fps is None else expected_fps)
    expected = frame_rate(fps)
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source_id must be a nonempty string")
    path = Path(video).expanduser()
    _local_ref(str(path))
    initial_stat = path.lstat()
    if not stat.S_ISREG(initial_stat.st_mode):
        raise ValueError("source must be an ordinary local file, not a symlink or directory")
    # lstat above already establishes existence and excludes a file symlink.
    # Non-strict resolution also works in Windows restricted temp directories
    # where final-handle path resolution can be denied despite readable files.
    path = path.resolve()
    initial = _identity(initial_stat)
    reference = path.as_posix() if ref is None else ref
    _local_ref(reference)
    executable = media_tool("ffprobe", ffprobe)
    with open_media_source(path) as incoming:
        if _identity(os.fstat(incoming.fileno())) != initial:
            raise ValueError("source file changed before verification lock")
        return _measure_locked(path, incoming, initial, source_id, reference, executable, timeout, fps, expected,
                               single_decode=_single_decode)


def _measure_locked(path, incoming, initial, source_id, reference, executable, timeout, fps, expected, *,
                    single_decode=False):
    deadline = time.monotonic() + timeout
    raw = _probe_details(path, executable, _remaining(deadline))
    metadata = normalize_metadata(raw, str(path))
    video_index = _integer(metadata["video"]["stream_index"], "video_stream_index")
    selected = [entry for entry in raw["streams"] if entry.get("index") == video_index]
    if len(selected) != 1:
        raise ValueError("missing or duplicate selected video stream")
    stream = selected[0]
    width = _integer(stream.get("width"), "width", maximum=16384)
    height = _integer(stream.get("height"), "height", maximum=16384)
    if not width or not height:
        raise ValueError("measured dimensions must be positive")
    _frame_properties(stream, width, height)
    rotation = _rotation(stream)
    if any(_ratio(stream.get(field), field) != expected for field in ("avg_frame_rate", "r_frame_rate")):
        raise ValueError("source reported fps does not match the explicitly expected fps")
    frames = _scan_frames(path, executable, stream, fps, deadline)
    # _scan_frames already decodes and counts every frame while validating
    # properties and the full CFR/PTS grid. A locked batch avoids decoding the
    # identical source/output again solely for a second count. Ordinary calls
    # retain the independent second pass.
    if not single_decode:
        counted = read_frame_count(path, video_index, executable, _remaining(deadline),
                                   count_frames=True, runner=_probe_text)
        if frames != counted:
            raise ValueError("frame timestamp count differs from independently decoded frame count")
    _check_identity(path, initial)
    if _identity(os.fstat(incoming.fileno())) != initial:
        raise ValueError("source file changed before hashing")
    hashed = hashlib.sha256()
    while chunk := incoming.read(1024 * 1024):
        _remaining(deadline)
        hashed.update(chunk)
    digest = hashed.hexdigest()
    if _identity(os.fstat(incoming.fileno())) != initial:
        raise ValueError("source file changed during hashing")
    _check_identity(path, initial)
    _remaining(deadline)
    audio = metadata["audio"]
    audio_index = None if audio is None else _integer(audio["stream_index"], "audio_stream_index")
    source = {"id": source_id, "ref": reference, "sha256": digest, "total_frames": frames,
              "width": width, "height": height, "fps": fps, "cfr": True, "progressive": True,
              "sample_aspect_num": 1, "sample_aspect_den": 1, "rotation_degrees": rotation,
              "colorspace": 709, "video_stream_index": video_index, "audio_stream_index": audio_index}
    profile = {"width": width, "height": height, "fps": deepcopy(fps)}
    schema = _load_schema()
    for value, definition in ((profile, "profile"), (source, "source")):
        _schema_validate(value, {"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]})
    _profile_sources(profile, [source])
    return {"profile": profile, "source": source}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-id", default="normalized")
    parser.add_argument("--ref", default=None)
    parser.add_argument("--ffprobe", default=None)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("manifest output already exists; use a new filename")
        data = build_media_manifest(args.video, source_id=args.source_id, ref=args.ref,
                                    ffprobe=args.ffprobe, timeout=args.timeout)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"INVALID: {error}\n")
    print(f"MANIFEST: {args.output} ({data['source']['total_frames']} measured CFR frames)")


if __name__ == "__main__":
    main()
