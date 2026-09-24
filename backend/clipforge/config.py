"""Resolve media executables at call time, without assuming a local install path."""

import os


def media_tool(name: str, explicit: str | None = None) -> str:
    """CLI/function override > FFMPEG/FFPROBE > legacy *_PATH > PATH command."""
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError("Unsupported media tool")
    key = name.upper()
    return explicit or os.environ.get(key) or os.environ.get(key + "_PATH") or name
