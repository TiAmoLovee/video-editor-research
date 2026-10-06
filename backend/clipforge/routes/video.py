"""视频上传、持久化状态查询与按清单下载。"""

from pathlib import Path, PureWindowsPath
import json
import logging
import shutil
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from clipforge.storage.jobs import create_job, get_job, job_dir, list_jobs, submission_unknown
from clipforge.queue.client import QueueUnavailable, submit_video
from clipforge.analysis.options import parse_options
from clipforge.decision.presentation import candidate_page
from clipforge.decision.selection import build_selection
from clipforge.decision.acceptance import load_accepted

router = APIRouter(prefix="/tasks", tags=["视频任务"])
logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


@router.get("", summary="查看历史视频任务")
def video_history(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return list_jobs(limit, offset)


@router.post("", status_code=202, summary="上传视频并提交后台处理",
             responses={413: {"description": "文件超过 1 GiB"},
                        415: {"description": "不支持的扩展名"},
                        503: {"description": "提交结果不确定，请保留返回的任务编号"}})
def upload_video(response: Response, file: UploadFile = File(...), shot_options: str | None = Form(None)):
    try:
        options = parse_options(shot_options)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
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
        if options is not None:
            (folder / "shot_options.json").write_text(
                json.dumps(options, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
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


def result_file(job, task_id, filename):
    if job["status"] != "SUCCEEDED":
        raise HTTPException(409, "任务尚未成功完成，暂无可下载结果。")
    relative = (job["result"] or {}).get("files", {}).get(filename)
    if relative is None:
        raise HTTPException(404, "下载文件不存在。")
    folder = job_dir(str(task_id)).resolve()
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder) or not target.is_file():
        raise HTTPException(404, "下载文件不存在。")
    return target


@router.get('/{task_id}/candidates', summary='分页查看候选片段排名及评分原因')
def ranked_candidates(task_id: UUID, limit: int = Query(20, ge=1, le=100),
                      offset: int = Query(0, ge=0), view: str = Query('all', pattern='^(all|retained|review)$')):
    job = require_job(task_id)
    target = result_file(job, task_id, 'candidates.json')
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
        page = candidate_page(data, limit, offset)
        report = selection_for_task(job, task_id, data)
        if report is not None:
            notes = {item['candidate_id']: item for item in report['items']}
            if view in ('retained', 'review'):
                retained = [c for c in data['candidates'] if (notes[c['id']]['retained'] if view == 'retained'
                            else notes[c['id']]['boundary_status'] == 'review_required')]
                # 原排名保留，分页基于全批去重结果，不在当前页内单独去重。
                page['items'] = [candidate_page(data, 1, c['rank'] - 1)['items'][0]
                                 for c in retained[offset:offset + limit]]
                page.update(total=len(retained), has_more=offset + limit < len(retained))
            for item in page['items']:
                item['selection'] = notes[item['id']]
            page['selection_summary'] = report['summary']
            page['selection_version'] = report['version']
            page['accepted_versions'] = report['accepted_versions']
        elif view != 'all':
            raise HTTPException(409, '此历史任务没有可核对的分析记录。')
        page['view'] = view
        return page
    except (OSError, ValueError, TypeError, KeyError) as error:
        logger.exception('Candidate read failed for task %s', task_id)
        raise HTTPException(500, '候选结果暂不可用，请查看后台日志或重新处理视频。') from error


def selection_for_task(job, task_id, candidates):
    if 'analysis.json' not in (job['result'] or {}).get('files', {}):
        return None
    analysis = json.loads(result_file(job, task_id, 'analysis.json').read_text(encoding='utf-8'))
    root = job_dir(str(task_id)).resolve()
    review_path = root / 'boundary_review.json'
    if not review_path.resolve().is_relative_to(root):
        raise ValueError('人工记录路径异常')
    review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else None
    report = build_selection(candidates, analysis, review)
    report['accepted_versions'] = load_accepted(root, str(task_id), analysis, candidates)
    return report


@router.get('/{task_id}/accepted/{filename}', summary='播放或下载已核对的人工验收成品')
def accepted_video(task_id: UUID, filename: str):
    job = require_job(task_id)
    try:
        data = json.loads(result_file(job, task_id, 'candidates.json').read_text(encoding='utf-8'))
        report = selection_for_task(job, task_id, data)
        if report is None or not any(filename == item['id']+'.mp4' for item in report['accepted_versions']):
            raise HTTPException(404, '没有对应的已验收成品。')
        return FileResponse(job_dir(str(task_id))/'accepted'/filename, media_type='video/mp4', filename=filename)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(500, '验收记录或成品校验失败。') from error


@router.get('/{task_id}/selection', summary='下载当前边界复核及去重记录；不调用模型')
def selection_download(task_id: UUID):
    job = require_job(task_id)
    try:
        data = json.loads(result_file(job, task_id, 'candidates.json').read_text(encoding='utf-8'))
        report = selection_for_task(job, task_id, data)
        if report is None:
            raise HTTPException(404, '此任务没有分析记录。')
        return JSONResponse(report, headers={'Content-Disposition': 'attachment; filename="selection.json"',
                                             'Cache-Control': 'no-store'})
    except (OSError, ValueError, TypeError, KeyError) as error:
        logger.exception('Selection export failed for task %s', task_id)
        raise HTTPException(500, '边界复核记录暂不可用，请查看后台日志。') from error


@router.get("/{task_id}/files/{filename}", summary="下载成品切片、JSON 或 ZIP")
def download_video_file(task_id: UUID, filename: str):
    target = result_file(require_job(task_id), task_id, filename)
    media_type = {".mp4": "video/mp4", ".json": "application/json", ".zip": "application/zip"}.get(target.suffix)
    return FileResponse(target, filename=filename, media_type=media_type)
