"""把本地视频转为 H.264 / AAC、恒定 30 fps 的 MP4。"""

from clipforge.config import media_tool

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from clipforge.media.probe import normalize_metadata, probe_video


def validate_output(metadata: dict, had_audio: bool) -> None:
    """检查成品的基本参数；平均帧率检查不替代逐帧时间戳验证。"""
    video = metadata["video"]
    if video["codec"] != "h264" or video["avg_frame_rate"] != "30/1":
        raise ValueError("成品的视频编码或平均帧率不符合 H.264 / 30 fps。")
    if metadata["duration_seconds"] is None:
        raise ValueError("无法读取成品的有效时长。")
    audio = metadata["audio"]
    if had_audio and (audio is None or audio["codec"] != "aac"):
        raise ValueError("输入有音轨，但成品没有符合要求的 AAC 音轨。")
    if not had_audio and audio is not None:
        raise ValueError("无音轨输入不应生成额外音轨。")


def normalize_video(
    video_path: str,
    output_path: str,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    timeout_seconds: int = 3600,
) -> dict:
    """读取素材、转码、检查结果，成功后返回成品元数据。"""
    ffmpeg_path = media_tool("ffmpeg", ffmpeg_path)
    ffprobe_path = media_tool("ffprobe", ffprobe_path)
    source = Path(video_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    if source == target:
        raise ValueError("输入和输出路径不能相同。")
    if not source.is_file():
        raise ValueError(f"输入视频不存在：{source}")
    if target.exists():
        raise ValueError(f"输出文件已存在，请使用新的文件名：{target}")
    if target.suffix.lower() != ".mp4":
        raise ValueError("输出文件必须使用 .mp4 扩展名。")
    if timeout_seconds <= 0:
        raise ValueError("超时时间必须大于零。")

    before = normalize_metadata(probe_video(str(source), ffprobe_path), str(source))
    had_audio = before["audio"] is not None
    target.parent.mkdir(parents=True, exist_ok=True)

    # 先在独立临时目录生成文件；失败会清理，不把半成品当作最终结果。
    with tempfile.TemporaryDirectory(prefix=".clipforge-", dir=target.parent) as folder:
        temporary = Path(folder) / "normalized.mp4"
        command = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostats",
            "-nostdin", "-n", "-i", str(source),
            "-map", f"0:{before['video']['stream_index']}",
        ]
        if had_audio:
            command += ["-map", f"0:{before['audio']['stream_index']}"]
        else:
            command += ["-an"]
        command += [
            "-vf", "fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "fast",
            "-crf", "20", "-pix_fmt", "yuv420p",
        ]
        if had_audio:
            command += ["-c:a", "aac", "-b:a", "128k"]
        command += ["-movflags", "+faststart", str(temporary)]

        try:
            subprocess.run(
                command, capture_output=True, encoding="utf-8", errors="replace",
                check=True, timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("找不到 FFmpeg，请设置 FFMPEG 环境变量或通过 --ffmpeg 指定路径。") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"转码超过 {timeout_seconds} 秒，已停止。") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"转码失败：{exc.stderr.strip() or '无详细信息'}") from exc

        after = normalize_metadata(probe_video(str(temporary), ffprobe_path), str(temporary))
        validate_output(after, had_audio)
        # 独占创建最终文件，避免检查后其他进程创建同名文件时被覆盖。
        # 复制成功才返回；复制失败时删除本次创建的残缺文件。
        with temporary.open("rb") as incoming:
            with target.open("xb") as outgoing:
                try:
                    shutil.copyfileobj(incoming, outgoing)
                except BaseException:
                    outgoing.close()
                    target.unlink()
                    raise
        after["source_file"] = target.name
        return after


def main() -> int:
    parser = argparse.ArgumentParser(description="归一化视频为 H.264 / AAC / CFR 30 fps")
    parser.add_argument("video", help="输入视频路径")
    parser.add_argument("output", help="输出 MP4 路径（必须是新文件）")
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--ffprobe", default=None)
    parser.add_argument("--timeout", type=int, default=3600, help="转码超时秒数，默认 3600")
    args = parser.parse_args()
    print("正在读取素材并转码，请等待……", file=sys.stderr)
    try:
        metadata = normalize_video(args.video, args.output, args.ffmpeg, args.ffprobe, args.timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False))
    print("转码完成，成品基本参数检查通过。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
