"""在归一化视频时间轴上运行 WebRTC VAD，输出独立的语音/非语音分区。"""

import argparse
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import wave

from clipforge.config import media_tool
from clipforge.media.probe import normalize_metadata, probe_video
from clipforge.media.split import read_frame_count

SAMPLE_RATE = 16000


def extract_pcm(source, output, stream_index, video_start, samples, ffmpeg, timeout=3600):
    """保留音视频相对偏移；补齐头尾，按视频时长截取，不把晚开始的音轨移到零。"""
    filters = (f"asetpts=PTS-({video_start})/TB,"
               f"aresample={SAMPLE_RATE}:async=1:first_pts=0,"
               f"apad=whole_len={samples},atrim=end_sample={samples}")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-xerror",
               "-copyts", "-i", str(source), "-map", f"0:{stream_index}", "-vn",
               "-af", filters, "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
               str(output)]
    try:
        subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace",
                       check=True, timeout=timeout)
    except FileNotFoundError as error:
        raise RuntimeError("找不到 FFmpeg，请检查 FFMPEG 配置") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("VAD 音频提取超时") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"VAD 音频提取失败：{error.stderr.strip()}") from error


def classify_pcm(path, duration, *, mode=2, frame_ms=30):
    """逐帧读取 PCM；末帧补零只供分类，结果末端严格限制在视频时长内。"""
    import webrtcvad

    detector = webrtcvad.Vad(mode)
    frame_samples = SAMPLE_RATE * frame_ms // 1000
    expected = math.ceil(duration * SAMPLE_RATE)
    speech, silence = [], []
    previous = None
    position = 0
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
            raise ValueError("VAD 要求 16 kHz 单声道 16-bit PCM")
        if audio.getnframes() != expected:
            raise ValueError("提取音频采样数与视频时间轴不一致")
        while position < expected:
            count = min(frame_samples, expected - position)
            chunk = audio.readframes(count)
            if len(chunk) != count * 2:
                raise ValueError("PCM 文件被截断，不能发布不完整 VAD 结果")
            active = detector.is_speech(chunk.ljust(frame_samples * 2, b"\0"), SAMPLE_RATE)
            start, end = position / SAMPLE_RATE, min((position + count) / SAMPLE_RATE, duration)
            intervals = speech if active else silence
            if active == previous:
                intervals[-1]["end"] = end
            else:
                intervals.append({"start": start, "end": end})
            previous = active
            position += count
    return speech, silence


def detect_speech(video_path, ffmpeg=None, ffprobe=None, *, mode=2, frame_ms=30):
    """无音轨返回 no_audio；有音轨但无人声返回覆盖全片的非语音区间。"""
    if type(mode) is not int or mode not in range(4):
        raise ValueError("mode 必须是 0、1、2 或 3")
    if type(frame_ms) is not int or frame_ms not in (10, 20, 30):
        raise ValueError("frame_ms 必须是 10、20 或 30 毫秒")
    source = Path(video_path).expanduser().resolve()
    started = time.perf_counter()
    probe = media_tool("ffprobe", ffprobe)
    raw = probe_video(str(source), probe)
    meta = normalize_metadata(raw, str(source))
    if meta["video"]["codec"] != "h264" or meta["video"]["avg_frame_rate"] != "30/1":
        raise ValueError("请先归一化为 H.264 / CFR 30 fps 再运行 VAD")
    frames = read_frame_count(source, meta["video"]["stream_index"], probe, 3600)
    duration = frames / 30
    stream = next(s for s in raw["streams"] if s["index"] == meta["video"]["stream_index"])
    video_start = float(stream.get("start_time", "0"))
    if not math.isfinite(video_start):
        raise ValueError("视频起始时间无效")
    has_audio = meta["audio"] is not None
    speech, silence = [], []
    if has_audio:
        with tempfile.TemporaryDirectory(prefix="clipforge-vad-") as directory:
            pcm = Path(directory) / "audio.wav"
            extract_pcm(source, pcm, meta["audio"]["stream_index"], video_start,
                        math.ceil(duration * SAMPLE_RATE), media_tool("ffmpeg", ffmpeg))
            speech, silence = classify_pcm(pcm, duration, mode=mode, frame_ms=frame_ms)
    with source.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    return {
        "schema_version": "0.1.0", "artifact_kind": "vad_analysis", "result_kind": "measured",
        "media": {"normalized_sha256": digest, "time_reference": "normalized_video",
                  "fps": 30, "total_frames": frames, "duration_seconds": duration,
                  "has_audio": has_audio},
        "analyzer": {"status": "ok" if has_audio else "no_audio", "tool": "WebRTC VAD",
                     "version": version("webrtcvad-wheels"), "model": None,
                     "parameters": {"mode": mode, "frame_ms": frame_ms, "sample_rate_hz": SAMPLE_RATE,
                                    "channels": 1, "sample_width_bits": 16,
                                    "merge_adjacent_equal_labels": True, "extra_smoothing": False,
                                    "last_frame": "zero_pad_for_detection_clamp_output"}},
        "speech": speech, "silence": silence,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--mode", type=int, default=2)
    parser.add_argument("--frame-ms", type=int, default=30)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.resolve() == args.video.resolve():
            raise ValueError("输出已存在或指向输入视频，请指定新文件")
        result = detect_speech(args.video, args.ffmpeg, args.ffprobe, mode=args.mode, frame_ms=args.frame_ms)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
    except (OSError, ValueError, RuntimeError, ImportError, wave.Error) as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(f"PASS: {len(result['speech'])} speech intervals -> {args.output}")


if __name__ == "__main__":
    main()
