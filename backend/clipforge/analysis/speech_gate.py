"""转写前使用随 faster-whisper 安装的 Silero 模型筛选语音。"""

import hashlib
from pathlib import Path

SPEECH_GATE_PARAMETERS = {
    "threshold": 0.5, "neg_threshold": 0.35,
    "min_speech_duration_ms": 0, "min_silence_duration_ms": 500, "speech_pad_ms": 200,
}


def prepare_speech(pcm, samples):
    from faster_whisper.audio import decode_audio
    from faster_whisper.utils import get_assets_path
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = decode_audio(str(pcm), sampling_rate=16000)
    if len(audio) != samples:
        raise ValueError("语音过滤输入采样数与视频时间轴不一致")
    chunks = get_speech_timestamps(audio, VadOptions(**SPEECH_GATE_PARAMETERS))
    model = Path(get_assets_path()) / "silero_vad_v6.onnx"
    with model.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    return audio, {
        "tool": "Silero VAD", "model": model.name, "model_sha256": digest,
        "parameters": dict(SPEECH_GATE_PARAMETERS),
        "speech_intervals": [{"start": c["start"] / 16000, "end": c["end"] / 16000} for c in chunks],
        "retained_seconds": sum(c["end"] - c["start"] for c in chunks) / 16000,
        "no_speech_skipped": not chunks,
    }
