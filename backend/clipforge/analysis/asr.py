"""faster-whisper 词级转写；输入保持归一化视频的完整时间轴。"""

from functools import lru_cache
import copy
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import tempfile
import time
import wave

from clipforge.analysis.model import model_spec, model_directory, verify_model
from clipforge.analysis.chinese import simplify_words
from clipforge.analysis.speech_gate import SPEECH_GATE_PARAMETERS, prepare_speech
from clipforge.analysis.sentences import SENTENCE_PARAMETERS, split_sentences
from clipforge.analysis.vad import SAMPLE_RATE, extract_pcm
from clipforge.config import media_tool
from clipforge.media.probe import normalize_metadata, probe_video
from clipforge.media.split import read_frame_count

TRANSCRIBE_PARAMETERS = {"beam_size": 5, "temperature": 0.0, "word_timestamps": True,
                         "vad_filter": True, "vad_parameters": dict(SPEECH_GATE_PARAMETERS),
                         "condition_on_previous_text": False,
                         "no_speech_threshold": 0.6, "log_prob_threshold": -1.0,
                         "compression_ratio_threshold": 2.4, "task": "transcribe"}

COVERAGE_RECOVERY_PARAMETERS = {
    "version": "local-word-coverage-recovery-v2", "max_windows": 2,
    "context_seconds": 5.0, "max_crop_seconds": 60.0,
    "min_gap_seconds": 3.0, "min_silero_speech_seconds": 3.0,
    "selection": "longest_gap_then_start", "attempts_per_gap": 1,
    "failure_policy": "stop_recovery_without_retry",
    "merge_policy": "insert_whole_words_inside_original_gap_only",
    "language_policy": "explicit_user_language_else_detect_inside_long_gap",
    "language_detection_min_probability": 0.5,
    "terminal_policy": "overhang_in_last_word_silero_interval_beyond_vad_padding",
    "terminal_decode": "one_bounded_context_crop_without_second_vad",
}


@lru_cache(maxsize=1)
def _load_model(directory, model_name):
    # 模型对象在单个 worker 进程内复用；不是分析结果缓存。
    identity = verify_model(directory, model_name)
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
                if words and bounded_start <= words[-1]["end"] + .05:
                    words[-1]["text"] += item.word
                    words[-1]["probability"] = None  # 合并后不冒充单个模型词的置信度。
                else:
                    # A zero-time token after a missing span belongs beside its
                    # own timestamp, never to a word tens of seconds earlier.
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


def splice_gap_words(original_words, recovery_words, gap, *, crop_timing_clamped=False):
    """Select whole recovered words; never trim or overwrite an original word."""
    accepted, excluded = [], []
    for word in recovery_words:
        reason = None
        if crop_timing_clamped:
            reason = "crop_timing_was_clamped"
        elif word["start"] < gap["start"] or word["end"] > gap["end"]:
            reason = "not_wholly_inside_original_gap"
        elif any(word["start"] < base["end"] and word["end"] > base["start"] for base in original_words):
            reason = "overlaps_original_word"
        if reason:
            excluded.append({"word": copy.deepcopy(word), "reason": reason})
        else:
            accepted.append(copy.deepcopy(word))
    return accepted, excluded


def _recovery_gaps(words, speech_gate, duration, coverage):
    """Keep long-gap coverage rules; also check a truncated final utterance.

    A syllable shorter than three seconds cannot satisfy the long-gap audit.
    Only a Silero interval containing the last word and continuing beyond its
    configured padding can request the extra check. Later unrelated activity,
    silence, and WebRTC/music disagreement do not qualify. This is decoding
    evidence, never permission to extend a candidate without measured words.
    """
    gaps = copy.deepcopy(coverage['suspect_intervals'])
    if not words or any(g['position'] in ('suffix', 'whole_media') for g in gaps):
        return gaps
    last = max(words, key=lambda w: w['end'])
    padding = speech_gate.get('parameters', SPEECH_GATE_PARAMETERS).get(
        'speech_pad_ms', SPEECH_GATE_PARAMETERS['speech_pad_ms']) / 1000
    for interval in speech_gate.get('speech_intervals', []):
        if (interval['start'] < last['end'] < interval['end']
                and last['end'] - last['start'] > 0
                and interval['end'] - last['end'] > padding + 1e-9):
            gaps.append({
                'start': last['end'], 'end': duration,
                'gap_seconds': duration - last['end'], 'position': 'suffix',
                'reason': 'terminal_speech_beyond_last_word',
                'silero_interval': copy.deepcopy(interval),
                'uncovered_speech_seconds': interval['end'] - last['end'],
                'speech_pad_seconds': padding,
            })
            break
    return gaps


def recover_word_coverage(audio, model, words, speech_gate, duration, *, language=None,
                          context_language=None):
    """Bounded local decoding of word-free gaps retained by the original VAD.

    The caller supplies the already verified model and full-timeline PCM array.
    Normal covered inputs cause no additional inference. Every original word
    is retained (apart from deterministic IDs); raw recovery segments are kept
    separately and can never be mistaken for the original decoder output.
    """
    from clipforge.analysis.coverage import audit_word_coverage

    parameters = dict(COVERAGE_RECOVERY_PARAMETERS)
    original = copy.deepcopy(words)

    def audit(current):
        return audit_word_coverage({
            "media": {"duration_seconds": duration}, "words": current,
            "analyzers": {"asr": {"parameters": {"speech_gate": speech_gate}}},
        }, min_gap_seconds=parameters["min_gap_seconds"],
           min_speech_seconds=parameters["min_silero_speech_seconds"])

    before = audit(original)
    report = {
        "version": parameters["version"], "parameters": parameters,
        "status": "not_needed", "attempt_count": 0, "inserted_word_count": 0,
        "original_word_count": len(original), "original_raw_segments_preserved": True,
        "coverage_before": before, "coverage_after": before, "attempts": [],
        "original_words": original, "word_origins": [],
        "interpretation": "Local ASR recovery is model evidence; it does not verify speech or event completeness.",
    }
    gaps = sorted(_recovery_gaps(original, speech_gate, duration, before),
                  key=lambda row: (-row["gap_seconds"], row["start"]))
    if not gaps:
        return copy.deepcopy(words), report
    samples = math.ceil(duration * SAMPLE_RATE)
    if len(audio) != samples:
        raise ValueError("ASR recovery PCM length differs from the full video timeline")

    inserted = []
    stopped = False
    for gap in gaps:
        crop_first = max(0, math.floor((gap["start"] - parameters["context_seconds"]) * SAMPLE_RATE))
        crop_last = min(samples, math.ceil((gap["end"] + parameters["context_seconds"]) * SAMPLE_RATE))
        crop_start, crop_end = crop_first / SAMPLE_RATE, crop_last / SAMPLE_RATE
        decode_parameters = copy.deepcopy(TRANSCRIBE_PARAMETERS)
        terminal = gap['reason'] == 'terminal_speech_beyond_last_word'
        if terminal:
            decode_parameters.pop('vad_parameters')
            decode_parameters.update(vad_filter=False, max_new_tokens=256)
        attempt = {"gap": copy.deepcopy(gap), "crop_start": crop_start, "crop_end": crop_end,
                   "crop_first_sample": crop_first, "crop_last_sample": crop_last,
                   "time_offset_seconds": crop_start, "requested_language": language,
                   "transcribe_parameters": decode_parameters}
        if crop_end - crop_start > parameters["max_crop_seconds"]:
            attempt.update(status="skipped", reason="crop_exceeds_bounded_duration")
            report["attempts"].append(attempt)
            continue
        if report["attempt_count"] >= parameters["max_windows"]:
            attempt.update(status="skipped", reason="bounded_attempt_limit")
            report["attempts"].append(attempt)
            continue
        report["attempt_count"] += 1
        attempt["attempt_number"] = report["attempt_count"]
        started = time.perf_counter()
        try:
            decode_language = language
            if language is None and not terminal:
                # Context may be spoken in another language. Detect on the
                # missing audio itself, not the five-second context prefix.
                first = math.floor(gap['start'] * SAMPLE_RATE)
                last = math.ceil(gap['end'] * SAMPLE_RATE)
                detected, probability, _ = model.detect_language(
                    audio=audio[first:last], vad_filter=False)
                if (not isinstance(detected, str) or not detected
                        or not math.isfinite(probability)
                        or not parameters['language_detection_min_probability'] <= probability <= 1):
                    raise ValueError('uncertain missing-span language; recovery stopped')
                decode_language = detected
                attempt['gap_language_detection'] = {
                    'language': detected, 'probability': probability,
                    'first_sample': first, 'last_sample': last,
                    'context_excluded': True,
                }
                if context_language is not None and detected != context_language:
                    # Decoding an English answer together with its Chinese
                    # question can translate the context and suppress the
                    # actual answer even when language='en'. Keep the target
                    # audio alone on an observed language switch.
                    crop_first, crop_last = first, last
                    crop_start, crop_end = first / SAMPLE_RATE, last / SAMPLE_RATE
                    attempt.update(crop_start=crop_start, crop_end=crop_end,
                        crop_first_sample=first, crop_last_sample=last,
                        time_offset_seconds=crop_start,
                        context_language=context_language,
                        context_removed_for_language_switch=True)
            attempt['decode_language'] = decode_language
            generated, info = model.transcribe(audio[crop_first:crop_last], language=decode_language,
                                               **decode_parameters)
            local_words, local_raw, adjustments = collect_words(generated, crop_end - crop_start)
            from clipforge.analysis.validation import _finite
            _finite({"words": local_words, "raw_segments": local_raw,
                     "language_probability": info.language_probability})
            # Do not insert words whose timing was clipped at the decoded crop.
            # Recovered words must also be wholly inside the ORIGINAL gap.
            global_words = copy.deepcopy(local_words)
            global_raw = copy.deepcopy(local_raw)
            for word in global_words:
                word["start"] += crop_start
                word["end"] += crop_start
            for segment in global_raw:
                segment["start"] += crop_start
                segment["end"] += crop_start
                for word in segment["words"]:
                    word["start"] += crop_start
                    word["end"] += crop_start
            attempt.update(detected_language=info.language, language_probability=info.language_probability,
                           decoded_words=global_words, local_raw_segments=local_raw,
                           raw_segments=global_raw, alignment_adjustments=adjustments,
                           excluded_words=[], inserted_crop_word_ids=[])
            accepted, excluded = splice_gap_words(
                original, global_words, gap,
                crop_timing_clamped=bool(adjustments["boundary_words_clamped"]))
            attempt["excluded_words"] = excluded
            attempt["inserted_crop_word_ids"] = [word["id"] for word in accepted]
            if not accepted:
                attempt.update(status="stopped", reason="no_safe_words_to_insert")
                stopped = True
            else:
                attempt.update(status="inserted", inserted_word_count=len(accepted))
                inserted.extend((word, attempt["attempt_number"]) for word in accepted)
        except Exception as error:
            attempt.update(status="stopped", reason="local_decode_failed", error_type=type(error).__name__)
            stopped = True
        attempt["elapsed_seconds"] = round(time.perf_counter() - started, 6)
        report["attempts"].append(attempt)
        if stopped:
            break

    entries = [(copy.deepcopy(word), {"source": "original", "original_word_id": word["id"]})
               for word in original]
    entries.extend((copy.deepcopy(word), {"source": "recovery", "attempt_number": attempt,
                                         "crop_word_id": word["id"]}) for word, attempt in inserted)
    entries.sort(key=lambda entry: (entry[0]["start"], entry[0]["end"], entry[0]["id"]))
    result = []
    for index, (word, origin) in enumerate(entries, 1):
        word["id"] = f"w{index:06d}"
        result.append(word)
        report["word_origins"].append({"word_id": word["id"], **origin})
    after = audit(result)
    remaining = _recovery_gaps(result, speech_gate, duration, after)
    report.update(inserted_word_count=len(inserted), coverage_after=after,
                  remaining_recovery_gaps=remaining)
    if stopped:
        report["status"] = "stopped_with_unverified_coverage"
    elif inserted:
        report["status"] = "recovered" if not remaining else "partially_recovered"
    else:
        report["status"] = "unresolved"
    return result, report


def pcm_has_signal(path, samples):
    """仅检查数字全零音频；语音筛选由独立的 Silero 步骤执行。"""
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
    spec = model_spec()
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
    speech_gate = None
    coverage_recovery = None
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
                audio, speech_gate = prepare_speech(pcm, math.ceil(duration * SAMPLE_RATE))
                # 全部非语音时不加载 Whisper，也不让空音频进入语言检测/解码。
                if not speech_gate["no_speech_skipped"]:
                    model, identity = _load_model(str(model_directory(model_dir, model_name=spec["name"])), spec["name"])
                    # 使用相同的 VAD 参数；库负责删去区间后的词级时间还原。
                    generated, info = model.transcribe(audio, language=requested_language, **TRANSCRIBE_PARAMETERS)
                    words, segments, diagnostics = collect_words(generated, duration)
                    detected_language, language_probability = info.language, info.language_probability
                    words, coverage_recovery = recover_word_coverage(
                        audio, model, words, speech_gate, duration,
                        language=requested_language, context_language=detected_language)
            else:
                digital_silence_skipped = True
    simplify_chinese = (requested_language or detected_language) == "zh"
    if simplify_chinese:
        words = simplify_words(words)
    with source.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    parameters = {**TRANSCRIBE_PARAMETERS, "requested_language": requested_language,
                  "detected_language": detected_language, "language_probability": language_probability,
                  "device": "cpu", "compute_type": "int8", "cpu_threads": 4, "num_workers": 1,
                  "model_revision": spec["revision"], "model_identity": identity,
                  "digital_silence_skipped": digital_silence_skipped,
                  "speech_gate": speech_gate,
                  "coverage_recovery_parameters": dict(COVERAGE_RECOVERY_PARAMETERS),
                  "coverage_recovery": coverage_recovery,
                  "text_normalization": {"tool": "opencc-python-reimplemented", "version": version("opencc-python-reimplemented"),
                                         "config": "t2s", "applied": simplify_chinese,
                                         "raw_segments_preserved": True},
                  "ctranslate2_version": version("ctranslate2"),
                  "sentence_rules": dict(SENTENCE_PARAMETERS), "alignment_adjustments": diagnostics}
    result = {
        "schema_version": "0.1.0", "artifact_kind": "asr_analysis", "result_kind": "measured",
        "media": {"normalized_sha256": digest, "time_reference": "normalized_video", "fps": 30,
                  "total_frames": frames, "duration_seconds": duration, "has_audio": has_audio},
        "analyzer": {"status": "ok" if has_audio else "no_audio", "tool": "faster-whisper",
                     "version": version("faster-whisper"), "model": spec["repository"], "parameters": parameters},
        "words": words, "sentences": split_sentences(words), "raw_segments": segments,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }
    # 包括原始分段也拒绝 NaN/Inf，不能把不合法数字留给 JSON 保存阶段。
    from clipforge.analysis.validation import _finite
    _finite(result)
    return result
