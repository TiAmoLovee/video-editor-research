"""读取本地视频的 FFprobe 信息：第一版暂时保留原始字段。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


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
        "r_frame_rate,avg_frame_rate,sample_rate,channels",
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


def main() -> int:
    parser = argparse.ArgumentParser(description="读取本地视频的基本信息")
    parser.add_argument("video", help="本地视频文件路径")
    parser.add_argument("--ffprobe", default="ffprobe", help="FFprobe 程序路径")
    args = parser.parse_args()

    try:
        metadata = probe_video(args.video, args.ffprobe)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
