"""复用已有媒体模块；由 Celery 调用，也可在隔离目录中测试。"""

import json
import logging
import os
from pathlib import Path
import zipfile

from job_store import claim_job, get_job, job_dir, update_job
from normalize import normalize_video
from probe import normalize_metadata, probe_video
from split import split_video

logger = logging.getLogger(__name__)


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def process_video(task_id: str) -> dict:
    if not claim_job(task_id):
        return {"task_id": task_id, "skipped": True}
    job = get_job(task_id)
    folder = job_dir(task_id)
    ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
    ffprobe = os.environ.get("FFPROBE_PATH", "ffprobe")
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

        stage, progress = "splitting", 60
        update_job(task_id, "RUNNING", stage, progress)
        plan = split_video(str(folder / "normalized.mp4"), str(folder / "clips"), ffmpeg, ffprobe)

        stage, progress = "packaging", 90
        update_job(task_id, "RUNNING", stage, progress)
        files = {
            "media_meta.json": "media_meta.json",
            "normalized_media_meta.json": "normalized_media_meta.json",
            "clip_plan.json": "clips/clip_plan.json",
        }
        for clip in plan["clips"]:
            files[clip["file"]] = f"clips/{clip['file']}"
        # 临时 ZIP 关闭后才发布；视频已经压缩，使用存储模式避免再次压缩。
        with zipfile.ZipFile(folder / "result.zip.tmp", "x", zipfile.ZIP_STORED) as archive:
            for name, relative_path in files.items():
                archive.write(folder / relative_path, name)
        (folder / "result.zip.tmp").rename(folder / "result.zip")
        files["result.zip"] = "result.zip"
        result = {"clip_count": len(plan["clips"]), "total_frames": plan["total_frames"],
                  "clips": plan["clips"], "files": files}
        update_job(task_id, "SUCCEEDED", "done", 100, result=result)
        return {"task_id": task_id, "clip_count": len(plan["clips"])}
    except Exception:
        logger.exception("Video task %s failed at %s", task_id, stage)
        (folder / "result.zip.tmp").unlink(missing_ok=True)
        update_job(task_id, "FAILED", stage, progress,
                   error=f"视频处理在 {stage} 阶段失败，请查看 worker 日志。")
        raise
