"""把已归一化的 CFR 30 fps MP4 按 900 帧（30 秒）切片。"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from probe import normalize_metadata, probe_video


FPS = 30
FRAMES_PER_CLIP = 900


def build_plan(total_frames: int) -> list[dict]:
    """按整数帧规划，end_frame 是不包含在片段内的结束位置。"""
    if type(total_frames) is not int or total_frames <= 0:
        raise ValueError("总帧数必须是正整数。")
    clips = []
    for start in range(0, total_frames, FRAMES_PER_CLIP):
        end = min(start + FRAMES_PER_CLIP, total_frames)
        clips.append({
            "file": f"clip_{len(clips) + 1:03d}.mp4",
            "start_frame": start,
            "end_frame": end,
            "frame_count": end - start,
            "start_seconds": start / FPS,
            "duration_seconds": (end - start) / FPS,
        })
    return clips


def run_tool(command: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8",
                                errors="replace", check=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到工具：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"工具运行超过 {timeout} 秒，已停止。") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"工具运行失败：{exc.stderr.strip() or '无详细信息'}") from exc
    return result.stdout


def read_frame_count(source: Path, stream_index: int, ffprobe: str,
                     timeout: int, count_frames: bool = False) -> int:
    """输入优先用帧数元数据；缺失时解码计数，成品可强制解码计数。"""
    command = [ffprobe, "-v", "error", "-select_streams", str(stream_index)]
    if count_frames:
        command += ["-count_frames"]
    command += ["-show_entries", "stream=nb_frames,nb_read_frames", "-of", "json", str(source)]
    data = json.loads(run_tool(command, timeout))
    streams = data.get("streams", [])
    if not streams:
        raise ValueError("未找到指定视频流。")
    value = streams[0].get("nb_read_frames" if count_frames else "nb_frames")
    try:
        frames = int(value)
        if frames <= 0:
            raise ValueError("没有有效帧")
        return frames
    except (TypeError, ValueError):
        if not count_frames:
            return read_frame_count(source, stream_index, ffprobe, timeout, True)
        raise ValueError("无法获得有效视频帧数。")


def split_video(video_path: str, output_dir: str, ffmpeg: str = "ffmpeg",
                ffprobe: str = "ffprobe", timeout: int = 3600) -> dict:
    source = Path(video_path).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"输入视频不存在：{source}")
    if target.exists():
        raise ValueError(f"输出目录已存在，请换一个新目录：{target}")
    if timeout <= 0:
        raise ValueError("超时时间必须大于零。")
    metadata = normalize_metadata(probe_video(str(source), ffprobe), str(source))
    video, audio = metadata["video"], metadata["audio"]
    if video["codec"] != "h264" or video["avg_frame_rate"] != "30/1":
        raise ValueError("请先使用 normalize.py 生成 H.264、CFR 30 fps 的输入。")
    if audio is not None and audio["codec"] != "aac":
        raise ValueError("输入音轨不是 AAC，请先归一化。")

    frames = read_frame_count(source, video["stream_index"], ffprobe, timeout)
    plan = {"plan_version": "1.0", "source_file": source.name, "fps": FPS,
            "total_frames": frames, "clips": build_plan(frames)}
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".clipforge-split-", dir=target.parent) as temp:
        staged = Path(temp) / "result"
        staged.mkdir()
        # 先有清单，再按清单渲染；后续可将该清单接入统一 EDL。
        for number, clip in enumerate(plan["clips"], start=1):
            print(f"正在切片 {number}/{len(plan['clips'])}：{clip['file']}", file=sys.stderr)
            output = staged / clip["file"]
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats",
                       "-nostdin", "-n", "-ss", f"{clip['start_frame'] / FPS:.9f}",
                       "-i", str(source), "-t", f"{clip['frame_count'] / FPS:.9f}",
                       "-map", f"0:{video['stream_index']}"]
            command += ["-map", f"0:{audio['stream_index']}"] if audio else ["-an"]
            command += ["-c:v", "libx264",
                        "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-fps_mode", "cfr"]
            if audio:
                command += ["-c:a", "aac", "-b:a", "128k"]
            command += ["-movflags", "+faststart", str(output)]
            run_tool(command, timeout)
            result = normalize_metadata(probe_video(str(output), ffprobe), str(output))
            if result["video"]["codec"] != "h264" or result["video"]["avg_frame_rate"] != "30/1":
                raise ValueError(f"{clip['file']} 的视频参数异常。")
            if audio and (result["audio"] is None or result["audio"]["codec"] != "aac"):
                raise ValueError(f"{clip['file']} 的音轨异常。")
            actual = read_frame_count(output, result["video"]["stream_index"], ffprobe, timeout, True)
            if actual != clip["frame_count"]:
                raise ValueError(f"{clip['file']} 预期 {clip['frame_count']} 帧，实际 {actual} 帧。")
        (staged / "clip_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            raise ValueError("输出目录在处理期间被创建，未覆盖，请换目录重试。")
        # Windows 下重命名不会替换已存在的目标目录。
        staged.rename(target)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="将归一化视频自动切成每段最多 30 秒的短片")
    parser.add_argument("video")
    parser.add_argument("output_dir", help="尚不存在的输出目录")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--timeout", type=int, default=3600, help="每次工具调用的超时秒数")
    args = parser.parse_args()
    try:
        plan = split_video(args.video, args.output_dir, args.ffmpeg, args.ffprobe, args.timeout)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"切片完成：共 {len(plan['clips'])} 段，帧数检查全部通过。")
    print(f"输出目录：{Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
