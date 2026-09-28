"""faster-whisper 词级转写；输入保持归一化视频的完整时间轴。"""

from functools import lru_cache
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import tempfile
import time
import wave

from clipforge.analysis.model import MODEL_ID, REVISION, model_directory, verify_model
from clipforge.analysis.sentences import SENTENCE_PARAMETERS, split_sentences
from clipforge.analysis.vad import SAMPLE_RATE, extract_pcm
from clipforge.config import media_tool
from clipforge.media.probe import normalize_metadata, probe_video
from clipforge.media.split import read_frame_count

TRANSCRIBE_PARAMETERS = {"beam_size": 5, "temperature": 0.0, "word_timestamps": True,
                         "vad_filter": False, "condition_on_previous_text": False,
                         "no_speech_threshold": 0.6, "log_prob_threshold": -1.0,
                         "compression_ratio_threshold": 2.4, "task": "transcribe"}


@lru_cache(maxsize=1)
def _load_model(directory):
    # 模型对象在单个 worker 进程内复用；不是分析结果缓存。
    identity = verify_model(directory)
    from faster_whisper import WhisperModel
    model = WhisperModel(directory, device="cpu", compute_type="int8", cpu_threads=4,
                         num_workers=1, local_files_only=True)
    return model, identity


def collect_words(segments, duration):
    """保留原词条；零时长词并入相邻有时长词，不编造其独立时间。"""
    words, raw_segments = [], []
    pending = ""
    merged, clamped = 0, 0
    for segment in segments:  # 消费生成器，确保推理真正完成且异常能传播。
        if segment.text.strip() and not segment.words:
            raise ValueError("转写有文字却没有词级时间戳，不能生成完整分析结果")
        raw = {"start": segment.start, "end": segment.end, "text": segment.text, "words": []}
        for item in segment.words or []:
            raw["words"].append({"start": item.start, "end": item.end,
                                 "text": item.word, "probability": item.probability})
            if not item.word.strip():
                continue
            start, end = float(item.start), float(item.end)
            probability = None if item.probability is None else float(item.probability)
            if (not math.isfinite(start) or not math.isfinite(end) or start > end
                    or start < -.05 or end > duration + .05
                    or (probability is not None and (not math.isfinite(probability) or not 0 <= probability <= 1))):
                raise ValueError("ASR 返回无效或超出视频范围的词级时间/概率")
            bounded_start, bounded_end = max(0.0, min(start, duration)), max(0.0, min(end, duration))
            clamped += (bounded_start, bounded_end) != (start, end)
            if bounded_start == bounded_end:
                merged += 1
                if words:
                    words[-1]["text"] += item.word
                    words[-1]["probability"] = None  # 合并后不冒充单个模型词的置信度。
                else:
                    pending += item.word
                continue
            if words and bounded_start < words[-1]["start"]:
                raise ValueError("ASR 词条顺序与时间顺序不一致")
            words.append({"id": f"w{len(words) + 1:06d}", "start": bounded_start, "end": bounded_end,
                          "text": pending + item.word, "probability": None if pending else probability})
            pending = ""
        raw_segments.append(raw)
    if pending:
        raise ValueError("转写只有零时长词，无法提供可靠的词级时间边界")
    return words, raw_segments, {"zero_duration_words_merged": merged, "boundary_words_clamped": clamped}


def pcm_has_signal(path, samples):
    """仅跳过数字全零音频；不按音量阈值删除轻声，也不使用 VAD 筛选词。"""
    nonzero = False
    count = 0
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) != (1, 2, SAMPLE_RATE, samples):
            raise ValueError("ASR PCM 格式或采样数不符合完整视频时间轴")
        while data := audio.readframes(32768):
            nonzero |= any(data)
            count += len(data)
    if count != samples * 2:
        raise ValueError("ASR PCM 音频被截断")
    return nonzero


def transcribe_video(video_path, ffmpeg=None, ffprobe=None, *, model_dir=None, language=None):
    source = Path(video_path).expanduser().resolve()
    started = time.perf_counter()
    probe = media_tool("ffprobe", ffprobe)
    raw = probe_video(str(source), probe)
    meta = normalize_metadata(raw, str(source))
    if meta["video"]["codec"] != "h264" or meta["video"]["avg_frame_rate"] != "30/1":
        raise ValueError("ASR 输入必须先归一化为 H.264 / CFR 30 fps")
    frames = read_frame_count(source, meta["video"]["stream_index"], probe, 3600)
    duration = frames / 30
    requested_language = language or os.environ.get("CLIPFORGE_ASR_LANGUAGE") or None
    has_audio = meta["audio"] is not None
    words, segments, diagnostics = [], [], {"zero_duration_words_merged": 0, "boundary_words_clamped": 0}
    detected_language, language_probability, identity = None, None, None
    digital_silence_skipped = False
    if has_audio:
        stream = next(s for s in raw["streams"] if s["index"] == meta["video"]["stream_index"])
        origin = float(stream.get("start_time", "0"))
        if not math.isfinite(origin):
            raise ValueError("视频起始时间无效")
        with tempfile.TemporaryDirectory(prefix="clipforge-asr-") as directory:
            pcm = Path(directory) / "audio.wav"
            extract_pcm(source, pcm, meta["audio"]["stream_index"], origin,
                        math.ceil(duration * SAMPLE_RATE), media_tool("ffmpeg", ffmpeg))
            if pcm_has_signal(pcm, math.ceil(duration * SAMPLE_RATE)):
                model, identity = _load_model(str(model_directory(model_dir)))
                generated, info = model.transcribe(str(pcm), language=requested_language, **TRANSCRIBE_PARAMETERS)
                words, segments, diagnostics = collect_words(generated, duration)
                detected_language, language_probability = info.language, info.language_probability
            else:
                digital_silence_skipped = True
    with source.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    parameters = {**TRANSCRIBE_PARAMETERS, "requested_language": requested_language,
                  "detected_language": detected_language, "language_probability": language_probability,
                  "device": "cpu", "compute_type": "int8", "cpu_threads": 4, "num_workers": 1,
                  "model_revision": REVISION, "model_identity": identity,
                  "digital_silence_skipped": digital_silence_skipped,
                  "ctranslate2_version": version("ctranslate2"),
                  "sentence_rules": dict(SENTENCE_PARAMETERS), "alignment_adjustments": diagnostics}
    result = {
        "schema_version": "0.1.0", "artifact_kind": "asr_analysis", "result_kind": "measured",
        "media": {"normalized_sha256": digest, "time_reference": "normalized_video", "fps": 30,
                  "total_frames": frames, "duration_seconds": duration, "has_audio": has_audio},
        "analyzer": {"status": "ok" if has_audio else "no_audio", "tool": "faster-whisper",
                     "version": version("faster-whisper"), "model": MODEL_ID, "parameters": parameters},
        "words": words, "sentences": split_sentences(words), "raw_segments": segments,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }
    # 包括原始分段也拒绝 NaN/Inf，不能把不合法数字留给 JSON 保存阶段。
    from clipforge.analysis.validation import _finite
    _finite(result)
    return result
