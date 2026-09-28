"""真实 FFmpeg/WebRTC VAD 检查；TestClient 经过上传下载，队列投递使用替身。"""

import argparse
from array import array
import json
import math
from pathlib import Path
import platform
import subprocess
import tempfile
import wave

from clipforge.analysis.vad import detect_speech, extract_pcm
from clipforge.config import media_tool
from clipforge.media.probe import probe_video
from integration_shots import exercise


def check_partition(result):
    duration = result["media"]["duration_seconds"]
    if not result["media"]["has_audio"]:
        assert result["analyzer"]["status"] == "no_audio"
        assert result["speech"] == result["silence"] == []
        return
    assert result["analyzer"]["status"] == "ok"
    cursor = 0
    for item in sorted(result["speech"] + result["silence"], key=lambda item: item["start"]):
        assert item["start"] == cursor and cursor < item["end"] <= duration, item
        cursor = item["end"]
    assert cursor == duration


def create_fixture(ffmpeg, path, kind, video_offset=0):
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-copyts",
               "-itsoffset", str(video_offset), "-f", "lavfi", "-i",
               "color=c=black:s=160x96:r=30:d=3.033333333"]
    if kind == "silence":
        command += ["-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=3.033333333"]
    elif kind == "delayed_tone":
        command += ["-itsoffset", str(video_offset + 1), "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=16000:duration=0.5"]
    command += ["-map", "0:v:0"]
    if kind != "no_audio":
        command += ["-map", "1:a:0", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-avoid_negative_ts", "disabled", str(path)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)


def check_offset(ffmpeg, ffprobe, source, pcm):
    raw = probe_video(str(source), ffprobe)
    video = next(s for s in raw["streams"] if s["codec_type"] == "video")
    audio = next(s for s in raw["streams"] if s["codec_type"] == "audio")
    origin = float(video["start_time"])
    duration = 91 / 30
    extract_pcm(source, pcm, audio["index"], origin, math.ceil(duration * 16000), ffmpeg)
    with wave.open(str(pcm), "rb") as file:
        assert file.getnframes() == math.ceil(duration * 16000)
        samples = array("h", file.readframes(file.getnframes()))
    def peak(start, end):
        return max(abs(sample) for sample in samples[int(start * 16000):int(end * 16000)])
    # 正弦波只验证对齐，不作为“人声”参考标签；AAC 边缘留出余量。
    assert peak(.2, .8) < 5, "晚开始的音轨被错误移动到视频开头"
    assert peak(1.1, 1.4) > 500, "音频有效段未对齐到视频时间轴"
    assert peak(1.8, 2.8) < 5, "音轨结束后的时间未补齐"
    result = detect_speech(source, ffmpeg, ffprobe)
    check_partition(result)
    return {"video_start_seconds": origin, "audio_start_seconds": float(audio["start_time"]),
            "leading_and_trailing_padding_passed": True, "vad_result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("请指定新的记录文件名")
    if args.video and not args.video.is_file():
        parser.error("自备视频不存在")
    ffmpeg, ffprobe = media_tool("ffmpeg"), media_tool("ffprobe")
    reports, offsets = [], []
    with tempfile.TemporaryDirectory(prefix="clipforge-vad-check-") as directory:
        folder = Path(directory)
        for kind in ("no_audio", "silence"):
            source = folder / f"{kind}.mp4"
            create_fixture(ffmpeg, source, kind)
            report = exercise(source, folder / f"{kind}-data")
            result = report["vad_result"]
            check_partition(result)
            assert result["speech"] == []
            if kind == "silence":
                assert result["silence"] == [{"start": 0, "end": result["media"]["duration_seconds"]}]
            report["reference_kind"] = "synthetic_silent_or_absent_audio_not_speech_accuracy_dataset"
            reports.append(report)
        for offset in (0, 5):
            source = folder / f"offset-{offset}.mp4"
            create_fixture(ffmpeg, source, "delayed_tone", offset)
            record = check_offset(ffmpeg, ffprobe, source, folder / f"offset-{offset}.wav")
            assert abs(record["video_start_seconds"] - offset) < .01
            offsets.append(record)
        if args.video:
            report = exercise(args.video, folder / "real-data")
            check_partition(report["vad_result"])
            report["reference_kind"] = "real_video_smoke_without_manual_speech_labels"
            reports.append(report)
    summary = {"scope": "native media and HTTP adapters; queue mocked; no Docker/Celery verification",
               "python": platform.python_version(), "results": reports, "timestamp_checks": offsets}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(reports)} pipelines, {len(offsets)} timestamp fixtures -> {args.output}")


if __name__ == "__main__":
    main()
