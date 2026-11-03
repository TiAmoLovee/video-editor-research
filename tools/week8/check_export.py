"""Read-only check of ClipForge MP4 identification metadata, not visible marks.

Exit 0: metadata contract passes; 1: contract/container mismatch; 2: input/probe
error. A zero exit status NEVER certifies visual marking or legal compliance.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from clipforge.compliance import EXPORT_TAGS, inspect_metadata

MAX_PROBE_BYTES = 1024 * 1024


def probe_metadata(path, executable, timeout):
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise ValueError("timeout must be greater than zero and at most 300 seconds")
    command = [executable, "-v", "error", "-show_entries",
               "stream=codec_type:format=format_name:format_tags=" + ",".join(EXPORT_TAGS),
               "-of", "json", str(path)]
    # Write diagnostics to bounded-lifetime files rather than retaining arbitrary
    # probe output in RAM; only a limited JSON prefix is read into the parser.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.run(command, stdout=output, stderr=errors, timeout=timeout, check=False)
        if process.returncode:
            raise ValueError("ffprobe could not inspect this file")
        output.seek(0)
        raw = output.read(MAX_PROBE_BYTES + 1)
    if len(raw) > MAX_PROBE_BYTES:
        raise ValueError("ffprobe output exceeds the JSON size limit")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("format"), dict):
        raise ValueError("ffprobe did not return container information")
    return data


def inspect_export(path, ffprobe="ffprobe", timeout=30):
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("input must be a regular media file")
    with source.open("rb") as incoming:
        before = hashlib.file_digest(incoming, "sha256").hexdigest()
    data = probe_metadata(source, ffprobe, timeout)
    with source.open("rb") as incoming:
        after = hashlib.file_digest(incoming, "sha256").hexdigest()
    if before != after:
        raise ValueError("input changed while it was being inspected")
    container = data["format"]
    names = container.get("format_name", "")
    streams = data.get("streams", [])
    mp4_video = (isinstance(names, str) and "mp4" in names.split(",")
                 and isinstance(streams, list)
                 and any(isinstance(item, dict) and item.get("codec_type") == "video" for item in streams))
    metadata = inspect_metadata(container.get("tags"))
    return {
        "schema_version": "1.0.0", "scope": "metadata-only",
        "file_name": source.name, "sha256": before,
        "mp4_video": mp4_video,
        "metadata": metadata,
        "metadata_check_passed": mp4_video and metadata["status"] == "pass",
        "visible_label": {"status": "not_checked", "expected_text": "AI辅助剪辑",
                          "reason": "Metadata cannot establish whether pixels contain a perceptible label."},
        "legal_compliance_assessed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, help="Optional new JSON file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        result = inspect_export(args.video, args.ffprobe, args.timeout)
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output is not None:
            with args.output.open("x", encoding="utf-8", newline="\n") as outgoing:
                outgoing.write(serialized)
        sys.stdout.write(serialized)
        return 0 if result["metadata_check_passed"] else 1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        sys.stderr.write("Export check failed: invalid input, probe failure, timeout, or output already exists.\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
