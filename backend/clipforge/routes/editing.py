"""Explicit candidate selections and immutable, measured EDL revisions."""

from collections.abc import Callable
import logging
import re
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from starlette.datastructures import Headers

from clipforge.services import editing as editing_service
from clipforge.services import editing_changes as editing_changes_service
from clipforge.services import editing_history as editing_history_service
from clipforge.services import mlt_exporting as mlt_exporting_service
from clipforge.services import rendering as rendering_service
from clipforge.services import subtitling as subtitling_service

router = APIRouter(prefix="/tasks", tags=["�����嵥"])
logger = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
T = TypeVar("T")
CandidateId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]


def _export_media_type(filename):
    return {"output.mp4": "video/mp4", "project.mlt": "application/xml", "captions.srt": "application/x-subrip",
            "captions.ass": "text/x-ssa"}.get(filename, "application/json")


class VerifiedDownload(StreamingResponse):
    """Serve full or single-range bytes from the same verified, held handle."""

    def __init__(self, resource, filename, media_type):
        self.resource = resource
        self.incoming = resource.__enter__()
        self.closed = False
        try:
            self.length = self.incoming.seek(0, 2)
            self.incoming.seek(0)
            self.remaining = self.length
            headers = {**NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"',
                       "Content-Length": str(self.length), "Accept-Ranges": "bytes"}
            super().__init__(self.iter_bytes(), media_type=media_type, headers=headers)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            self.resource.__exit__(None, None, None)

    def iter_bytes(self):
        try:
            while self.remaining:
                chunk = self.incoming.read(min(1024 * 1024, self.remaining))
                if not chunk:
                    break
                self.remaining -= len(chunk)
                yield chunk
        finally:
            self.close()

    def apply_range(self, value):
        """Select one inclusive byte range; multipart requests use a full response."""
        if value is None:
            return None
        if len(value) > 1024:
            return 400
        units, separator, bounds = value.partition("=")
        if not separator:
            return 400
        if units.strip().lower() != "bytes" or "," in bounds:
            return None
        match = re.fullmatch(r"([0-9]*)-([0-9]*)", bounds.strip())
        if match is None or not any(match.groups()):
            return 400
        first, last = match.groups()
        if first:
            start = int(first)
            end = int(last) + 1 if last else self.length
            if last and end <= start:
                return 400
            if start >= self.length:
                return 416
            end = min(end, self.length)
        else:
            suffix = int(last)
            if suffix == 0 or self.length == 0:
                return 416
            start, end = max(0, self.length - suffix), self.length
        self.incoming.seek(start)
        self.remaining = end - start
        self.status_code = 206
        self.headers["content-length"] = str(self.remaining)
        self.headers["content-range"] = f"bytes {start}-{end - 1}/{self.length}"
        return None

    async def __call__(self, scope, receive, send):
        try:
            if scope["type"] == "http" and scope["method"] == "GET":
                headers = Headers(scope=scope)
                # No strong ETag/date is published, so an If-Range cannot match.
                status = self.apply_range(headers.get("range")) if "if-range" not in headers else None
                if status is not None:
                    error_headers = {**NO_STORE, "Accept-Ranges": "bytes"}
                    if status == 416:
                        error_headers["Content-Range"] = f"bytes */{self.length}"
                    error = JSONResponse(status_code=status, content={"detail": "��Ч�򲻿�������ֽڷ�Χ��"},
                                         headers=error_headers)
                    await error(scope, receive, send)
                    return
            await super().__call__(scope, receive, send)
        finally:
            self.close()


class CreateEdlRequest(BaseModel):
    """Keep candidate IDs in the requested output order, without coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)
    selected_ids: list[CandidateId] = Field(min_length=1, max_length=100)

    @field_validator("selected_ids")
    @classmethod
    def distinct_nonblank_ids(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("��ѡ��Ų���Ϊ�հס�")
        if len(set(values)) != len(values):
            raise ValueError("��ѡ��Ų����ظ���")
        return values


class EditClipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate_id: CandidateId
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(ge=1)


class SaveEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    clips: list[EditClipRequest] = Field(min_length=1, max_length=100)
    editing_elapsed_ms: int | None = Field(default=None, ge=0, le=86_400_000)

    @field_validator("clips")
    @classmethod
    def valid_clips(cls, values):
        if (any(not value.candidate_id.strip() or value.source_in_frame >= value.source_out_frame for value in values)
                or len({value.candidate_id for value in values}) != len(values)):
            raise ValueError("Ƭ�α���벻ͬ��Դ��֡��С��ֹ֡��")
        return values


def _public_call(operation: Callable[[], T]) -> T:
    """Expose only the service's deliberately safe error messages."""
    try:
        return operation()
    except editing_service.ProjectError as error:
        raise HTTPException(error.status_code, error.detail) from None
    except Exception:
        logger.exception("Unexpected editing API failure")
        raise HTTPException(500, "�����嵥�����ݲ����ã������̨��־��") from None


@router.post(
    "/{task_id}/edl", status_code=201, summary="��ָ����ѡ˳�򴴽������嵥�汾",
    description=("�ύ 1�C100 �����ظ��ĺ�ѡ��ţ�˳�����˳��"
                 "ͬ���˶Թ�һ����Ƶ��ԭ������ý����鹲�� 90 �볬ʱԤ�㡣"),
    responses={404: {"description": "���񲻴���"},
               409: {"description": "����ý����ѡ�����㴴������"},
               503: {"description": "ý�����򱣴�ʧ��"}},
)
def create_edl(task_id: UUID, payload: CreateEdlRequest) -> JSONResponse:
    def operation():
        revision = editing_service.create_task_edl(str(task_id), payload.selected_ids, timeout=90)
        revision_id = str(UUID(str(revision["revision_id"])))
        return JSONResponse(
            status_code=201, content=jsonable_encoder(revision),
            headers={**NO_STORE, "Location": f"/tasks/{task_id}/edl/{revision_id}"},
        )
    return _public_call(operation)


@router.get(
    "/{task_id}/edl/{revision_id}", summary="��ȡ�ѱ���ļ����嵥��ý���嵥",
    responses={404: {"description": "�����汾������"},
               409: {"description": "������嵥����Դ��¼�Ѹı�"}},
)
def get_edl(task_id: UUID, revision_id: UUID) -> JSONResponse:
    def operation():
        saved = editing_service.load_task_edl(str(task_id), str(revision_id))
        available = editing_changes_service.available_edit_clips(str(task_id), str(revision_id), saved)
        return JSONResponse(content=jsonable_encoder({**saved, **available}), headers=NO_STORE)
    return _public_call(operation)


@router.post(
    "/{task_id}/edl/{revision_id}/edits", status_code=201,
    summary="��Դ֡�����µ��˹��༭�汾���޸���",
    description="����������ɻ��ߺ͸��汾��ֻ�ı��ѡѡ��Դ�е��˳��δ��ʱ��գ��������ֻ���ý�塣",
)
def save_edl_edit(task_id: UUID, revision_id: UUID, payload: SaveEditRequest) -> JSONResponse:
    def operation():
        record = editing_changes_service.create_task_edit(
            str(task_id), str(revision_id), [clip.model_dump() for clip in payload.clips],
            editing_elapsed_ms=payload.editing_elapsed_ms)
        new_id = str(UUID(str(record["revision_id"])))
        return JSONResponse(status_code=201, content=jsonable_encoder(record), headers={
            **NO_STORE, "Location": f"/tasks/{task_id}/edl/{new_id}"})
    return _public_call(operation)


@router.post(
    '/{task_id}/edl/{revision_id}/portrait', status_code=201,
    summary='�ӱ���ĺ��� EDL �����̶����Ĳü��� 9:16 �汾',
    description='����ԭ�ز�������Ƭ��˳����е㣻�����°汾����� 1080��1920������ʶ������֡�',
)
def create_portrait(task_id: UUID, revision_id: UUID) -> JSONResponse:
    def operation():
        record = editing_service.create_task_portrait(str(task_id), str(revision_id))
        new_id = str(UUID(str(record['revision_id'])))
        return JSONResponse(status_code=201, content=jsonable_encoder(record), headers={
            **NO_STORE, 'Location': f'/tasks/{task_id}/edl/{new_id}'})
    return _public_call(operation)


@router.get(
    "/{task_id}/edl/{revision_id}/files/{filename}", summary="���ذ汾�� EDL ����Դ����",
    response_class=StreamingResponse,
    responses={404: {"description": "�ļ����ڴ˰汾�������嵥��"},
               409: {"description": "����İ汾У��ʧ��"}},
)
def download_edl_file(task_id: UUID, revision_id: UUID, filename: str) -> StreamingResponse:
    return _public_call(lambda: VerifiedDownload(
        editing_service.open_revision_file(str(task_id), str(revision_id), filename),
        filename=filename, media_type="application/json",
    ))


@router.get(
    "/{task_id}/edl/{revision_id}/source", summary="���ؾ� SHA256 �˶ԵĹ�һ���ز�",
    response_class=StreamingResponse,
    responses={404: {"description": "�����汾������"},
               409: {"description": "�ز�ȱʧ���뱣��İ汾��һ��"}},
)
def download_edl_source(task_id: UUID, revision_id: UUID) -> StreamingResponse:
    return _public_call(lambda: VerifiedDownload(
        editing_service.open_revision_source(str(task_id), str(revision_id)),
        filename="normalized.mp4", media_type="video/mp4",
    ))


@router.post(
    "/{task_id}/edl/{revision_id}/renders", status_code=201,
    summary="��ָ�����ѱ��� EDL �汾��ȾΪ MP4",
    description=("��ָ���汾����� EDL ��Ⱦ�������ܺ�ѡѡ�����Ⱦ������"
                 "�زĺ��顢ת��ͳ�Ƭ���鹲�� 90 �볬ʱԤ�㡣"),
    responses={404: {"description": "����� EDL �汾������"},
               409: {"description": "EDL ����Դý��У��ʧ��"},
               503: {"description": "ת����Ƭ����ʧ��"}},
)
def create_render(task_id: UUID, revision_id: UUID) -> JSONResponse:
    def operation():
        rendered = rendering_service.create_task_render(str(task_id), str(revision_id), timeout=90)
        render_id = str(UUID(str(rendered["render_id"])))
        return JSONResponse(
            status_code=201, content=jsonable_encoder(rendered),
            headers={**NO_STORE, "Location": f"/tasks/{task_id}/edl/{revision_id}/renders/{render_id}"},
        )
    return _public_call(operation)


@router.get(
    "/{task_id}/edl/{revision_id}/renders/{render_id}", summary="��ȡ��Ƭ��¼�ͺ��鱨��",
    responses={404: {"description": "����EDL �汾���Ƭ������"},
               409: {"description": "��Ƭ��¼���ļ�У��ʧ��"}},
)
def get_render(task_id: UUID, revision_id: UUID, render_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(
        content=jsonable_encoder(rendering_service.load_task_render(
            str(task_id), str(revision_id), str(render_id))),
        headers=NO_STORE,
    ))


@router.get(
    "/{task_id}/edl/{revision_id}/renders/{render_id}/files/{filename}",
    summary="���س�Ƭ�����鱨�����Ⱦʹ�õ� EDL ����", response_class=StreamingResponse,
    responses={404: {"description": "�ļ����ڴ˳�Ƭ�������嵥��"},
               409: {"description": "��Ƭ��¼�������ļ�У��ʧ��"}},
)
def download_render_file(task_id: UUID, revision_id: UUID, render_id: UUID,
                         filename: str) -> StreamingResponse:
    return _public_call(lambda: VerifiedDownload(
        rendering_service.open_render_file(str(task_id), str(revision_id), str(render_id), filename),
        filename=filename, media_type=_export_media_type(filename),
    ))


@router.post(
    "/{task_id}/edl/{revision_id}/subtitles", status_code=201,
    summary="�ӱ���� EDL ��תд������Ļ�汾",
    description="��ʹ�ô� EDL �汾�ķ������գ���Դ֡ӳ����Ļ��������ʶ�����������ģ�͡�",
)
def create_subtitles(task_id: UUID, revision_id: UUID) -> JSONResponse:
    def operation():
        record = subtitling_service.create_task_subtitles(str(task_id), str(revision_id))
        subtitle_id = str(UUID(str(record["subtitle_id"])))
        return JSONResponse(status_code=201, content=jsonable_encoder(record), headers={
            **NO_STORE, "Location": f"/tasks/{task_id}/edl/{revision_id}/subtitles/{subtitle_id}"})
    return _public_call(operation)


@router.get("/{task_id}/edl/{revision_id}/subtitles/{subtitle_id}", summary="��ȡ��Ļ�汾��Դʱ��ӳ��")
def get_subtitles(task_id: UUID, revision_id: UUID, subtitle_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        subtitling_service.load_task_subtitles(str(task_id), str(revision_id), str(subtitle_id))), headers=NO_STORE))


@router.get(
    "/{task_id}/edl/{revision_id}/subtitles/{subtitle_id}/files/{filename}",
    summary="���� SRT��ASS ����Ļʱ��ӳ��", response_class=StreamingResponse,
)
def download_subtitle_file(task_id: UUID, revision_id: UUID, subtitle_id: UUID, filename: str) -> StreamingResponse:
    return _public_call(lambda: VerifiedDownload(
        subtitling_service.open_subtitle_file(str(task_id), str(revision_id), str(subtitle_id), filename),
        filename=filename, media_type=_export_media_type(filename),
    ))


@router.post(
    "/{task_id}/edl/{revision_id}/subtitles/{subtitle_id}/renders", status_code=201,
    summary="��ͬһ EDL ��¼ָ���ı�����Ļ",
    description="���÷��������õ���Ļ������ FFmpeg���˶���Ļ��Դ����¼������ 90 �봦��Ԥ�㡣",
)
def create_subtitled_render(task_id: UUID, revision_id: UUID, subtitle_id: UUID) -> JSONResponse:
    def operation():
        record = rendering_service.create_task_render(
            str(task_id), str(revision_id), subtitle_id=str(subtitle_id), timeout=90)
        render_id = str(UUID(str(record["render_id"])))
        return JSONResponse(status_code=201, content=jsonable_encoder(record), headers={
            **NO_STORE, "Location": f"/tasks/{task_id}/edl/{revision_id}/renders/{render_id}"})
    return _public_call(operation)


def _create_mlt_export(task_id, revision_id, subtitle_id=None):
    def operation():
        record = mlt_exporting_service.create_task_mlt_export(
            str(task_id), str(revision_id), subtitle_id=str(subtitle_id) if subtitle_id is not None else None)
        export_id = str(UUID(str(record["export_id"])))
        return JSONResponse(status_code=201, content=jsonable_encoder(record), headers={
            **NO_STORE, "Location": f"/tasks/{task_id}/edl/{revision_id}/exports/mlt/{export_id}"})
    return _public_call(operation)


@router.post(
    "/{task_id}/edl/{revision_id}/exports/mlt", status_code=201,
    summary="�ӱ���� EDL �������ɱ�� MLT ���̰汾",
    description="�����Ѻ���Ĺ�һ���زģ�����Ƭ��˳���е�ͱ���Ĳ��У�������ʶ������֡�",
)
def create_mlt_export(task_id: UUID, revision_id: UUID) -> JSONResponse:
    return _create_mlt_export(task_id, revision_id)


@router.post(
    "/{task_id}/edl/{revision_id}/subtitles/{subtitle_id}/exports/mlt", status_code=201,
    summary="��ͬһ EDL ������������Ļ�� MLT ���̰汾",
    description="�˶Ա������Ļ��Դ���������ƣ�ʹ�þ�ȷʱ����֡��Χ�������ܿͻ�����Ļ����в�����",
)
def create_subtitled_mlt_export(task_id: UUID, revision_id: UUID, subtitle_id: UUID) -> JSONResponse:
    return _create_mlt_export(task_id, revision_id, subtitle_id)


@router.get("/{task_id}/edl/{revision_id}/exports/mlt/{export_id}", summary="��ȡ�Ѻ���� MLT ������¼")
def get_mlt_export(task_id: UUID, revision_id: UUID, export_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        mlt_exporting_service.load_task_mlt_export(str(task_id), str(revision_id), str(export_id))), headers=NO_STORE))


@router.get(
    "/{task_id}/edl/{revision_id}/exports/mlt/{export_id}/files/{filename}",
    summary="���ؾ������ MLT ���̻���Դ����", response_class=StreamingResponse,
)
def download_mlt_export_file(task_id: UUID, revision_id: UUID, export_id: UUID, filename: str) -> StreamingResponse:
    return _public_call(lambda: VerifiedDownload(
        mlt_exporting_service.open_mlt_export_file(str(task_id), str(revision_id), str(export_id), filename),
        filename=filename, media_type=_export_media_type(filename)))


@router.get("/{task_id}/edl", summary="��ȡ�������ͨ��У��ļ����嵥��ʷ")
def list_edls(task_id: UUID, limit: Annotated[int, Query(ge=1, le=50)] = 10,
              offset: Annotated[int, Query(ge=0, le=500)] = 0) -> JSONResponse:
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        editing_history_service.list_task_edls(str(task_id), limit=limit, offset=offset)), headers=NO_STORE))


@router.get("/{task_id}/edl/{revision_id}/artifacts", summary="��ȡ�˱���汾�Ѻ������Ļ�뵼����ʷ")
def list_edl_artifacts(task_id: UUID, revision_id: UUID) -> JSONResponse:
    return _public_call(lambda: JSONResponse(content=jsonable_encoder(
        editing_history_service.list_revision_artifacts(str(task_id), str(revision_id))), headers=NO_STORE))
