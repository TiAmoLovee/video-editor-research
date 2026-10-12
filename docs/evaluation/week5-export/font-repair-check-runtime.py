"""Collect all export-runtime gaps; no rendering, project data or HTTP calls."""
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

FILTERS = {"ass", "trim", "settb", "setpts", "setsar", "scale", "format", "crop", "concat",
           "aresample", "aformat", "atrim", "asetpts", "apad", "anullsrc"}
TABLES = {"filters": FILTERS, "encoders": {"libx264", "aac", "rawvideo"},
          "decoders": {"h264", "pcm_s16le"}, "demuxers": {"mov"}, "muxers": {"mp4", "rawvideo"}}


def capability_names(output):
    names = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[A-Z.]{1,6}", parts[0]):
            names.update(parts[1].split(","))
    return names


def call(arguments):
    return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30).stdout


def reviewed_api():
    spec = importlib.util.find_spec("clipforge")
    if spec is None or spec.origin != "/app/backend/clipforge/__init__.py":
        raise ImportError("API code is not the reviewed /app/backend binding")
    importlib.import_module("clipforge.api")
    return spec.origin


def font_details(font):
    from clipforge.editing.render import _font_info
    data = font.read_bytes()
    glyphs = _font_info(data, "Noto Sans CJK SC", {ord(char) for char in "容器字幕验收保存顺序AI辅助剪辑"})
    return {"file": str(font), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "family": "Noto Sans CJK SC", "glyph_count": glyphs["glyph_count"]}


def main():
    report = {"status": "started", "checks": {}, "issues": [], "model_calls": 0, "network": "none",
              "renders": 0, "http_requests": 0, "worker_started": False}

    def check(name, action):
        try:
            value = action()
            report["checks"][name] = value
            return value
        except Exception as error:  # noqa: BLE001 -- Report every gap together instead of stopping at the first.
            report["issues"].append({"check": name, "error_type": type(error).__name__, "error": str(error)})
            return None

    def require(condition, message, value=True):
        if not condition:
            raise RuntimeError(message)
        return value

    check("non_root_uid", lambda: require(os.geteuid() == 10001, "Expected non-root UID 10001", os.geteuid()))
    versions = {}
    for name in ("fastapi", "uvicorn", "jsonschema", "celery", "redis", "pydantic", "python-multipart"):
        versions[name] = check("python:" + name, lambda name=name: version(name))
    check("api_import", reviewed_api)
    index = Path(os.environ.get("CLIPFORGE_FRONTEND_DIST", "/app/frontend/dist")) / "index.html"
    check("frontend_index", lambda: require(index.is_file(), "Bundled frontend index.html is missing", str(index)))
    font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if check("font_readable", lambda: require(font.is_file() and os.access(font, os.R_OK), "Required Noto CJK font is missing/unreadable")):
        check("font_family_and_glyphs", lambda: font_details(font))
    found = {}
    for binary in ("ffmpeg", "ffprobe"):
        found[binary] = check(binary, lambda binary=binary: require(bool(shutil.which(binary)), binary + " is missing", shutil.which(binary)))
        if found[binary]:
            check(binary + "_version", lambda binary=binary: call([binary, "-version"]).splitlines()[0])
    if found["ffmpeg"]:
        for table, needed in TABLES.items():
            def verify(table=table, needed=needed):
                actual = capability_names(call(["ffmpeg", "-hide_banner", "-loglevel", "error", "-" + table]))
                missing = sorted(needed - actual)
                return require(not missing, "FFmpeg " + table + " missing: " + ", ".join(missing), sorted(needed))
            check("ffmpeg_" + table, verify)
        help_text = check("ffmpeg_cli_help", lambda: call(["ffmpeg", "-hide_banner", "-h", "full"]))
        if help_text is not None:
            del report["checks"]["ffmpeg_cli_help"]
            options = {"-fps_mode", "-enc_time_base", "-filter_complex_script"}
            check("ffmpeg_cli_options", lambda: require(all(option in help_text for option in options), "FFmpeg lacks saved-EDL command options", sorted(options)))
    if shutil.which("dpkg-query"):
        for package in ("ffmpeg", "fonts-noto-cjk"):
            check("debian:" + package, lambda package=package: call(["dpkg-query", "-W", "-f=${Version}", package]).strip())
    report["dependency_versions"] = versions
    report["status"] = "failed_image_capabilities" if report["issues"] else "passed_image_capabilities_not_export_acceptance"
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
