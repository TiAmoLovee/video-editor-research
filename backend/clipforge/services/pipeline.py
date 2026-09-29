"""复用已有媒体模块；由 Celery 调用，也可在隔离目录中测试。"""

import json
import logging
import os
import time
from contextlib import ExitStack
from clipforge.config import media_tool
from pathlib import Path
import zipfile

from clipforge.storage.jobs import claim_job, get_job, job_dir, update_job
from clipforge.media.normalize import normalize_video
from clipforge.media.probe import normalize_metadata, probe_video
from clipforge.media.split import split_video
from clipforge.analysis.shots import detect_shots
from clipforge.analysis.options import detector_options
from clipforge.analysis.vad import detect_speech
from clipforge.analysis.asr import transcribe_video
from clipforge.analysis.combine import combine_analysis
from clipforge.services import result_cache

logger = logging.getLogger(__name__)


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def process_video(task_id: str) -> dict:
    if not claim_job(task_id):
        return {"task_id": task_id, "skipped": True}
    started = time.perf_counter()
    job = get_job(task_id)
    folder = job_dir(task_id)
    source = folder / job['source_key']
    info = {'format_version': 1, 'hit': False, 'status': 'disabled',
            'transcription_executed': True, 'reused_stages': [], 'key': None}
    with ExitStack() as stack:
        index = descriptor = None
        if os.environ.get('CLIPFORGE_RESULT_CACHE', '1') != '0':
            try:
                key, descriptor = result_cache.request_fingerprint(
                    source, folder/'shot_options.json', media_tool('ffmpeg'), media_tool('ffprobe'))
                index = stack.enter_context(result_cache.key_lock(key))
                info.update(status='miss', key=key)
            except Exception:
                # 缓存不是处理成功的前提；模型/工具问题仍由原处理步骤准确报告。
                logger.warning('Result cache unavailable; processing normally', exc_info=True)
                info['status'] = 'unavailable'
                index = None
        try:
            restored = None
            if index is not None:
                try:
                    restored = result_cache.restore(index, descriptor, source, folder)
                except (OSError, ValueError, KeyError, TypeError):
                    logger.warning('Invalid cache ignored for task %s', task_id, exc_info=True)
                    info['status'] = 'invalid'
            if restored:
                info.update(hit=True, status='hit', transcription_executed=False,
                            reused_from_task_id=restored['task_id'],
                            reused_stages=['normalizing', 'analyzing_shots', 'analyzing_speech',
                                           'transcribing', 'combining_analysis', 'splitting'])
                meta = json.loads((folder/'media_meta.json').read_text(encoding='utf-8'))
                meta['source_file'] = job['source_name']
                save_json(folder/'media_meta.json', meta)
                previous = restored['result']
                plan = {'clips': previous['clips'], 'total_frames': previous['total_frames']}
                return finish_task(task_id, folder, plan, dict(restored['files']), info, started)
            result = _process_uncached(task_id, info, started)
            if index is not None:
                try:
                    result_cache.publish(index, descriptor, task_id, source)
                except Exception:
                    # 分析成功而索引写入失败时，保留本次成功结果，下次继续正常处理。
                    logger.warning('Result cache publication failed for %s', task_id, exc_info=True)
            return result
        except Exception:
            current = get_job(task_id)
            if current and current['status'] == 'RUNNING':
                update_job(task_id, 'FAILED', current['stage'], current['progress'],
                           error='视频结果复用或打包失败，请查看 worker 日志。')
            raise


def finish_task(task_id, folder, plan, files, info, started):
    update_job(task_id, 'RUNNING', 'packaging', 90)
    info['worker_seconds_before_packaging'] = round(time.perf_counter()-started, 6)
    info['timing_scope'] = 'worker processing before ZIP; excludes upload, queue wait, ZIP and cache publication'
    info['component_elapsed_seconds_are_original'] = bool(info['hit'])
    save_json(folder/'cache.json', info)
    files['cache.json'] = 'cache.json'
    try:
        with zipfile.ZipFile(folder / 'result.zip.tmp', 'x', zipfile.ZIP_STORED) as archive:
            for name, relative_path in files.items():
                archive.write(folder / relative_path, name)
        (folder/'result.zip.tmp').rename(folder/'result.zip')
    except Exception:
        (folder/'result.zip.tmp').unlink(missing_ok=True)
        raise
    files['result.zip'] = 'result.zip'
    result = {'clip_count': len(plan['clips']), 'total_frames': plan['total_frames'],
              'clips': plan['clips'], 'files': files}
    update_job(task_id, 'SUCCEEDED', 'done', 100, result=result)
    return {'task_id': task_id, 'clip_count': len(plan['clips']), 'cache_hit': info['hit']}


def _process_uncached(task_id, cache_info, started):
    job = get_job(task_id)
    folder = job_dir(task_id)
    ffmpeg = media_tool("ffmpeg")
    ffprobe = media_tool("ffprobe")
    stage, progress = "probing", 10
    try:
        source = folder / job["source_key"]
        original = normalize_metadata(probe_video(str(source), ffprobe), str(source))
        original["source_file"] = job["source_name"]
        save_json(folder / "media_meta.json", original)

        stage, progress = "normalizing", 25
        update_job(task_id, "RUNNING", stage, progress)
        normalized = normalize_video(str(source), str(folder / "normalized.mp4"), ffmpeg, ffprobe)
        save_json(folder / "normalized_media_meta.json", normalized)

        stage, progress = "analyzing_shots", 45
        update_job(task_id, "RUNNING", stage, progress)
        options_path = folder / "shot_options.json"
        shot_settings = (detector_options(json.loads(options_path.read_text(encoding="utf-8")), normalized)
                         if options_path.is_file() else {})
        shot_analysis = detect_shots(folder / "normalized.mp4", ffprobe, **shot_settings)
        save_json(folder / "shots.json", shot_analysis)

        stage, progress = "analyzing_speech", 55
        update_job(task_id, "RUNNING", stage, progress)
        vad_analysis = detect_speech(folder / "normalized.mp4", ffmpeg, ffprobe)
        save_json(folder / "vad.json", vad_analysis)

        stage, progress = "transcribing", 60
        update_job(task_id, "RUNNING", stage, progress)
        asr_analysis = transcribe_video(folder / "normalized.mp4", ffmpeg, ffprobe)
        save_json(folder / "asr.json", asr_analysis)

        stage, progress = "combining_analysis", 65
        update_job(task_id, "RUNNING", stage, progress)
        analysis = combine_analysis(source, shot_analysis, vad_analysis, asr_analysis)
        save_json(folder / "analysis.json", analysis)

        stage, progress = "splitting", 70
        update_job(task_id, "RUNNING", stage, progress)
        plan = split_video(str(folder / "normalized.mp4"), str(folder / "clips"), ffmpeg, ffprobe)

        stage, progress = "packaging", 90
        update_job(task_id, "RUNNING", stage, progress)
        files = {
            "media_meta.json": "media_meta.json",
            "normalized_media_meta.json": "normalized_media_meta.json",
            "clip_plan.json": "clips/clip_plan.json",
            "shots.json": "shots.json",
            "vad.json": "vad.json",
            "asr.json": "asr.json",
            "analysis.json": "analysis.json",
        }
        if options_path.is_file():
            files["shot_options.json"] = "shot_options.json"
        for clip in plan["clips"]:
            files[clip["file"]] = f"clips/{clip['file']}"
        return finish_task(task_id, folder, plan, files, cache_info, started)
    except Exception:
        logger.exception("Video task %s failed at %s", task_id, stage)
        (folder / "result.zip.tmp").unlink(missing_ok=True)
        update_job(task_id, "FAILED", stage, progress,
                   error=f"视频处理在 {stage} 阶段失败，请查看 worker 日志。")
        raise
