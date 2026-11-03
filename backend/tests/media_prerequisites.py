"""Explicit prerequisites for optional real-media marking regressions."""
import os
import re
import subprocess
import unittest


def require_ass_marking(ffmpeg):
    """Skip unavailable features; configured tools or font errors must still fail."""
    if not os.environ.get("SUBTITLE_FONT_FILE") or not os.environ.get("SUBTITLE_FONT_NAME"):
        raise unittest.SkipTest("real marked video requires SUBTITLE_FONT_FILE and SUBTITLE_FONT_NAME")
    result = subprocess.run([ffmpeg, "-hide_banner", "-filters"], check=True,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if not re.search(r"^\s*[A-Z.]{2,3}\s+ass\s+V->V(?:\s|$)", result.stdout, re.MULTILINE):
        raise unittest.SkipTest("real marked video requires the FFmpeg libass filter")
