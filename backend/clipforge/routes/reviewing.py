"""Candidate review decisions and downloadable human-operation evidence."""

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from clipforge.routes.editing import NO_STORE, _public_call
from clipforge.services import reviewing

router = APIRouter(prefix="/tasks", tags=["人工复核与审计"])
CandidateId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
Actor = Annotated[str, StringConstraints(strict=True)]
Reason = Annotated[str, StringConstraints(strict=True)]
SnapshotHash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate_id: CandidateId
    decision: Literal["accepted", "rejected", "undecided"]
    actor: Actor
    request_id: Annotated[str, StringConstraints(strict=True)]
    reason: Reason | None = None
    expected_snapshot_sha256: SnapshotHash | None = None

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value):
        try:
            return reviewing._request_id(value)
        except reviewing.editing.ProjectError as error:
            raise ValueError(error.detail) from error

    @field_validator("candidate_id")
    @classmethod
    def nonblank_candidate(cls, value):
        if not value.strip():
            raise ValueError("候选编号不能为空白。")
        return value

    @field_validator("actor")
    @classmethod
    def supplied_name(cls, value):
        try:
            return reviewing.normalize_actor(value)
        except reviewing.editing.ProjectError as error:
            raise ValueError(error.detail) from error

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        try:
            return reviewing._reason(value)
        except reviewing.editing.ProjectError as error:
            raise ValueError(error.detail) from error


@router.get("/{task_id}/reviews", summary="读取当前候选快照的人工复核状态")
def get_reviews(task_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(
        content=jsonable_encoder(reviewing.review_state(str(task_id))), headers=NO_STORE))


@router.post("/{task_id}/reviews", status_code=201, summary="记录一次可恢复的人工复核决定",
             description="姓名由操作者填写，并非认证身份；request_id 幂等。采纳不保存 EDL，驳回不删除素材。")
def create_review(task_id: UUID, payload: ReviewRequest) -> JSONResponse:
    def operation():
        event = reviewing.create_review(
            str(task_id), payload.candidate_id, payload.decision, payload.actor,
            reason=payload.reason, request_id=str(payload.request_id),
            expected_snapshot_sha256=payload.expected_snapshot_sha256)
        return JSONResponse(status_code=201, content=jsonable_encoder(event), headers={
            **NO_STORE, "Location": f"/tasks/{task_id}/reviews/requests/{payload.request_id}"})
    return _public_call(operation)


@router.get("/{task_id}/reviews/requests/{request_id}", summary="按请求编号核对已提交的复核结果")
def get_review_request(task_id: UUID, request_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        reviewing.review_request(str(task_id), str(request_id))), headers=NO_STORE))


@router.get("/{task_id}/audit", summary="读取人工复核和剪辑修改的前后值与操作者记录")
def get_audit(task_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        reviewing.task_audit(str(task_id))), headers=NO_STORE))


@router.get("/{task_id}/audit/download", summary="下载当前任务的人工操作审计 JSON")
def download_audit(task_id: UUID) -> Response:
    def operation():
        body = json.dumps(reviewing.task_audit(str(task_id)), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        return Response(content=body.encode("utf-8"), media_type="application/json", headers={
            **NO_STORE, "Content-Disposition": f'attachment; filename="audit-{task_id}.json"'})
    return _public_call(operation)
