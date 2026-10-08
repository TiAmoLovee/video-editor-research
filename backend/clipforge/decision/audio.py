"""从哈希匹配的归一化视频提取 100 ms 音量帧，不接受来源不明的 WAV。"""

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import tempfile
import wave

import numpy as np

from clipforge.analysis.vad import SAMPLE_RATE, extract_pcm
from clipforge.config import media_tool
from clipforge.media.probe import normalize_metadata, probe_video
from clipforge.media.split import read_frame_count


def dbfs(amplitude):
    return max(-120.0, 20 * math.log10(max(float(amplitude), 1e-6)))


@dataclass(frozen=True)
class AudioProfile:
    normalized_sha256: str
    duration_seconds: float
    pcm_sha256: str
    rms: tuple
    peaks: tuple
    baseline_dbfs: float
    frame_seconds: float = 0.1

    def metadata(self):
        return {"status": "measured", "normalized_sha256": self.normalized_sha256,
                "duration_seconds": self.duration_seconds, "pcm_sha256": self.pcm_sha256,
                "sample_rate_hz": SAMPLE_RATE, "frame_seconds": self.frame_seconds,
                "baseline_dbfs": self.baseline_dbfs,
                "baseline_method": "median RMS of frames above -60 dBFS",
                "window_method": "frame centers inside candidate; max RMS and sample peak"}

    def window(self, start, end):
        # 全片固定分帧，首尾按帧中心选取；时间量化误差小于一帧（100 ms）。
        indices = [i for i in range(max(0, int(start / self.frame_seconds) - 1),
                                    min(len(self.rms), math.ceil(end / self.frame_seconds) + 1))
                   if start <= (i * self.frame_seconds
                                + min((i + 1) * self.frame_seconds, self.duration_seconds)) / 2 < end]
        peak_rms = max((self.rms[i] for i in indices), default=0)
        sample_peak = max((self.peaks[i] for i in indices), default=0)
        return {"peak_rms_dbfs": round(dbfs(peak_rms), 6),
                "sample_peak_dbfs": round(dbfs(sample_peak), 6),
                "baseline_dbfs": self.baseline_dbfs,
                "frame_count": len(indices)}


def _read_pcm(path, duration, normalized_hash):
    expected = math.ceil(duration * SAMPLE_RATE)
    rms, peaks = [], []
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
            raise ValueError("音量分析需要 16kHz 单声道 16-bit PCM")
        if audio.getnframes() != expected:
            raise ValueError("音量采样数与视频时间轴不一致")
        position = 0
        while position < expected:
            count = min(1600, expected - position)
            raw = audio.readframes(count)
            if len(raw) != count * 2:
                raise ValueError("PCM 文件不完整")
            digest.update(raw)
            samples = np.frombuffer(raw, dtype='<i2').astype(np.float64) / 32768
            rms.append(float(np.sqrt(np.mean(samples * samples))))
            peaks.append(float(np.max(np.abs(samples))))
            position += count
    active = [dbfs(value) for value in rms if dbfs(value) > -60]
    baseline = round(float(np.median(active)), 6) if active else -120.0
    return AudioProfile(normalized_hash, duration, digest.hexdigest(), tuple(rms), tuple(peaks), baseline)


def measure_audio(video, analysis, *, ffmpeg=None, ffprobe=None):
    source = Path(video).resolve()
    with source.open('rb') as stream:
        identity = hashlib.file_digest(stream, 'sha256').hexdigest()
    if identity != analysis['media']['normalized_sha256']:
        raise ValueError("归一化视频哈希不匹配，禁止混用音量与分析结果")
    probe = media_tool('ffprobe', ffprobe)
    raw = probe_video(str(source), probe)
    meta = normalize_metadata(raw, str(source))
    if meta['video']['codec'] != 'h264' or meta['video']['avg_frame_rate'] != '30/1':
        raise ValueError("音量输入必须是 H.264 / CFR 30 fps 归一化视频")
    duration = read_frame_count(source, meta['video']['stream_index'], probe, 3600) / 30
    if not math.isclose(duration, analysis['media']['duration_seconds'], abs_tol=1e-6, rel_tol=0):
        raise ValueError("视频时长与分析不一致")
    if (meta['audio'] is not None) != analysis['media']['has_audio']:
        raise ValueError("视频音轨与分析不一致")
    if meta['audio'] is None:
        return None
    video_stream = next(s for s in raw['streams'] if s['index'] == meta['video']['stream_index'])
    video_start = float(video_stream.get('start_time', '0'))
    if not math.isfinite(video_start):
        raise ValueError("视频起始时间无效")
    with tempfile.TemporaryDirectory(prefix='clipforge-volume-') as directory:
        pcm = Path(directory) / 'audio.wav'
        extract_pcm(source, pcm, meta['audio']['stream_index'], video_start,
                    math.ceil(duration * SAMPLE_RATE), media_tool('ffmpeg', ffmpeg))
        profile = _read_pcm(pcm, duration, identity)
    # 防止读取过程中视频被替换。
    with source.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != identity:
            raise ValueError("提取期间视频内容发生变化")
    return profile
