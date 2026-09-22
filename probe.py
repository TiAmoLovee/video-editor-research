"""读取本地视频信息，转换为 ClipForge media_meta 1.0 格式。"""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from fractions import Fraction


def probe_video(video_path: str, ffprobe_path: str = "ffprobe") -> dict:
    """检查素材、调用 FFprobe，并把 JSON 文本解析为 Python 字典。"""
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"视频文件不存在或不是普通文件：{source}")

    # 使用参数列表，带空格或中文的路径也作为一个完整参数传递。
    command = [
        ffprobe_path,
        "-v", "error",
        "-show_entries",
        "format=duration,format_name:"
        "stream=index,codec_type,codec_name,width,height,"
        "r_frame_rate,avg_frame_rate,sample_rate,channels:"
        "stream_disposition=attached_pic",
        "-of", "json",
        str(source),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 FFprobe，请通过 --ffprobe 指定程序路径。") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("读取视频信息超过 30 秒，已停止本次读取。") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or "FFprobe 未提供具体原因"
        raise RuntimeError(f"FFprobe 读取失败：{detail}") from exc

    # stdout 是程序的正常输出；loads 把 JSON 文本转换成字典和列表。
    metadata = json.loads(result.stdout)
    if not isinstance(metadata, dict):
        raise ValueError("FFprobe 返回的 JSON 不是预期的对象。")
    streams = metadata.get("streams", [])
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise ValueError("素材中没有视频流。")
    return metadata


def positive_number(value, integer=False):
    """缺失、N/A、零和非法数值统一用 None（JSON null）表示。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if integer:
        return int(number) if number.is_integer() else None
    return number


def normalize_metadata(raw: dict, video_path: str) -> dict:
    """选第一条非封面视频流和第一条音频流，形成固定的数据结构。"""
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic", 0)), None)
    if video is None:
        raise ValueError("素材中没有可处理的视频流（封面图片不算视频）。")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # 保留精确分数，例如 30000/1001；平均帧率不能证明素材为恒定帧率。
    try:
        rate = Fraction(str(video.get("avg_frame_rate")))
        if rate <= 0:
            raise ValueError("帧率不是正数")
        fps = float(rate)
        if not math.isfinite(fps):
            raise ValueError("帧率超出范围")
        ratio = f"{rate.numerator}/{rate.denominator}"
    except (ValueError, ZeroDivisionError, OverflowError):
        fps, ratio = None, None

    media_format = raw.get("format", {})
    return {
        "schema_version": "1.0",
        "source_file": Path(video_path).name,
        "duration_seconds": positive_number(media_format.get("duration")),
        "container_format": media_format.get("format_name") or None,
        "video": {
            "stream_index": video["index"],
            "codec": video.get("codec_name") or None,
            "width": positive_number(video.get("width"), integer=True),
            "height": positive_number(video.get("height"), integer=True),
            "avg_fps": fps,
            "avg_frame_rate": ratio,
        },
        "audio": None if audio is None else {
            "stream_index": audio["index"],
            "codec": audio.get("codec_name") or None,
            "sample_rate_hz": positive_number(audio.get("sample_rate"), integer=True),
            "channels": positive_number(audio.get("channels"), integer=True),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="读取本地视频的基本信息")
    parser.add_argument("video", help="本地视频文件路径")
    parser.add_argument("--ffprobe", default="ffprobe", help="FFprobe 程序路径")
    parser.add_argument("--output", help="可选：把结果保存为 UTF-8 JSON 文件")
    args = parser.parse_args()

    try:
        raw = probe_video(args.video, args.ffprobe)
        metadata = normalize_metadata(raw, args.video)
        content = json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            target = Path(args.output).expanduser().resolve()
            source = Path(args.video).expanduser().resolve()
            if target == source or (target.exists() and target.samefile(source)):
                raise ValueError("输出文件不能覆盖输入视频。")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content + "\n", encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
