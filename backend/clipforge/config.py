"""Resolve media executables at call time, without assuming a local install path."""

import os
import math


class SourceDurationError(ValueError):
    """Safe, user-facing source-duration policy failure."""


def validate_source_duration(metadata: dict) -> None:
    """Enforce the current worker policy on probed or hash-verified cached metadata."""
    raw = os.environ.get('CLIPFORGE_MAX_SOURCE_SECONDS', '7200')
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = 0
    if not 1 <= limit <= 86400:
        raise SourceDurationError('[SOURCE_DURATION_POLICY] 视频时长上限配置无效，请联系管理员。')
    duration = metadata.get('duration_seconds')
    if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0):
        raise SourceDurationError('[SOURCE_DURATION_UNKNOWN] 无法读取视频的有效时长，请检查或重新封装素材。')
    if duration > limit:
        raise SourceDurationError(f'[SOURCE_DURATION_LIMIT] 视频超过当前 {limit} 秒时长上限，请先裁剪后再上传。')


def media_tool(name: str, explicit: str | None = None) -> str:
    """CLI/function override > FFMPEG/FFPROBE > legacy *_PATH > PATH command."""
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError("Unsupported media tool")
    key = name.upper()
    return explicit or os.environ.get(key) or os.environ.get(key + "_PATH") or name
