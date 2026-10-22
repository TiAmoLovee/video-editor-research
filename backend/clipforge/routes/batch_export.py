"""Explicit saved-revision batch export and verified, allowlisted downloads."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from clipforge.routes.editing import NO_STORE, VerifiedDownload
from clipforge.services import batch_export as service
from clipforge.services.editing import ProjectError

router = APIRouter(prefix="/tasks", tags=["批量导出"])
logger = logging.getLogger(__name__)


class BatchExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


def _public_call(operation):
    try:
        return operation()
    except ProjectError as error:
        raise HTTPException(error.status_code, error.detail) from None
    except Exception:
        logger.exception("Unexpected batch export API failure")
        raise HTTPException(500, "批量导出服务暂不可用，请先刷新本版本记录并检查后台日志。") from None


@router.post("/{task_id}/edl/{revision_id}/exports/batch", status_code=201,
             summary="按保存版本的最终切点逐条渲染 MP4 并打包 ZIP",
             description="用户明确触发；request_id 保证同版本请求幂等。最多 100 段、18000 帧、512 MiB；渲染和核验共享 90 秒预算。")
def create_batch_export(task_id: UUID, revision_id: UUID, payload: BatchExportRequest):
    def operation():
        metadata = service.create_task_batch_export(str(task_id), str(revision_id), str(payload.request_id), timeout=90)
        export_id = str(UUID(metadata["export_id"]))
        if export_id != str(payload.request_id):
            raise ValueError("batch export request identity mismatch")
        return JSONResponse(status_code=201, content=jsonable_encoder(metadata), headers={
            **NO_STORE, "Location": f"/tasks/{task_id}/edl/{revision_id}/exports/batch/{export_id}"})
    return _public_call(operation)


@router.get("/{task_id}/edl/{revision_id}/exports/batch", summary="读取此保存版本的已核验批量导出历史")
def list_batch_exports(task_id: UUID, revision_id: UUID):
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        service.list_task_batch_exports(str(task_id), str(revision_id))), headers=NO_STORE))


@router.get("/{task_id}/edl/{revision_id}/exports/batch/{export_id}", summary="核验批量导出及每条片段清单")
def get_batch_export(task_id: UUID, revision_id: UUID, export_id: UUID):
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        service.load_task_batch_export(str(task_id), str(revision_id), str(export_id))), headers=NO_STORE))


@router.get("/{task_id}/edl/{revision_id}/exports/batch/{export_id}/files/{filename}",
            summary="下载已核验的批量 ZIP、EDL 或片段清单", response_class=StreamingResponse)
def download_batch_export_file(task_id: UUID, revision_id: UUID, export_id: UUID, filename: str):
    return _public_call(lambda: VerifiedDownload(
        service.open_batch_export_file(str(task_id), str(revision_id), str(export_id), filename),
        filename, "application/zip" if filename == "clips.zip" else "application/json"))
