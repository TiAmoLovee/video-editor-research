"""视频上传、持久化状态查询与按清单下载。"""

from pathlib import Path, PureWindowsPath
import shutil
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from job_store import create_job, get_job, job_dir, submission_unknown
from queue_client import QueueUnavailable, submit_video

router = APIRouter(prefix="/tasks", tags=["视频任务"])
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


@router.post("", status_code=202, summary="上传视频并提交后台处理",
             responses={413: {"description": "文件超过 1 GiB"},
                        415: {"description": "不支持的扩展名"},
                        503: {"description": "提交结果不确定，请保留返回的任务编号"}})
def upload_video(response: Response, file: UploadFile = File(...)):
    # 浏览器文件名只用于显示；Windows 和 POSIX 路径前缀均去除。
    source_name = PureWindowsPath(file.filename or "").name
    suffix = Path(source_name).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(415, "请选择 MP4、MOV、MKV、WebM、M4V 或 AVI 视频。")
    if len(source_name) > 240:
        raise HTTPException(422, "文件名过长，请缩短后再上传。")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "当前单个视频最大为 1 GiB。")
    task_id = str(uuid4())
    folder = job_dir(task_id)
    folder.mkdir(parents=True, exist_ok=False)
    source_key = "source" + suffix
    try:
        size = 0
        with (folder / source_key).open("xb") as destination:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "当前单个视频最大为 1 GiB。")
                destination.write(chunk)
        if size == 0:
            raise HTTPException(422, "上传文件为空。")
        create_job(task_id, source_name, source_key)
    except BaseException:
        # 只清理刚由服务生成的 UUID 目录；该任务尚未发送到队列。
        shutil.rmtree(folder)
        raise
    finally:
        file.file.close()

    status_url = f"/tasks/{task_id}"
    try:
        submit_video(task_id)
    except QueueUnavailable:
        # 网络错误不证明消息未投递，保留数据并允许 worker 抢占此状态。
        submission_unknown(task_id)
        return JSONResponse(status_code=503, headers={"Location": status_url}, content={
            "task_id": task_id, "status_url": status_url,
            "detail": "队列提交未获确认，请先查询此任务，避免重复上传。",
        })
    response.headers["Location"] = status_url
    return {"task_id": task_id, "status_url": status_url}


def require_job(task_id: UUID) -> dict:
    job = get_job(str(task_id))
    if job is None:
        raise HTTPException(404, "任务不存在。")
    return job


@router.get("/{task_id}", summary="查询视频任务阶段和下载地址")
def video_status(task_id: UUID):
    job = require_job(task_id)
    result = job["result"]
    response = {key: job[key] for key in (
        "task_id", "source_name", "status", "stage", "progress", "created_at", "updated_at", "error"
    )}
    response["progress_kind"] = "stage_estimate"
    response["result"] = None
    if job["status"] == "SUCCEEDED" and result:
        response["result"] = {
            "clip_count": result["clip_count"], "total_frames": result["total_frames"],
            "clips": [{**clip, "download_url": f"/tasks/{task_id}/files/{clip['file']}"}
                      for clip in result["clips"]],
            "downloads": {name: f"/tasks/{task_id}/files/{name}" for name in result["files"]},
        }
    return response


@router.get("/{task_id}/files/{filename}", summary="下载成品切片、JSON 或 ZIP")
def download_video_file(task_id: UUID, filename: str):
    job = require_job(task_id)
    if job["status"] != "SUCCEEDED":
        raise HTTPException(409, "任务尚未成功完成，暂无可下载结果。")
    relative = (job["result"] or {}).get("files", {}).get(filename)
    if relative is None:
        raise HTTPException(404, "下载文件不存在。")
    folder = job_dir(str(task_id)).resolve()
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder) or not target.is_file():
        raise HTTPException(404, "下载文件不存在。")
    media_type = {".mp4": "video/mp4", ".json": "application/json", ".zip": "application/zip"}.get(target.suffix)
    return FileResponse(target, filename=filename, media_type=media_type)
