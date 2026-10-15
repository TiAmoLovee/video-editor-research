"""Task-level transcript playback without requiring or creating an EDL revision."""

from uuid import UUID

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from clipforge.routes.editing import VerifiedDownload, _public_call
from clipforge.services.task_media import open_task_source

router = APIRouter(prefix="/tasks", tags=["转写播放"])


@router.get("/{task_id}/source", summary="播放与转写对应的归一化视频",
            response_class=StreamingResponse)
def task_source(task_id: UUID):
    return _public_call(lambda: VerifiedDownload(
        open_task_source(str(task_id)), filename="normalized.mp4", media_type="video/mp4"))
