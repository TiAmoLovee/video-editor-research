"""FastAPI 服务：健康检查与后台队列演示接口。"""

from uuid import UUID

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from queue_client import QueueUnavailable, read_result, submit_add
from video_api import router as video_router

app = FastAPI(
    title="ClipForge MVP-0",
    description="上传视频、后台归一化与固定 30 秒切片；支持任务查询和结果下载。",
    version="0.3.0",
)
app.include_router(video_router)


class AddRequest(BaseModel):
    left: int = Field(default=2, strict=True, ge=-1000000, le=1000000)
    right: int = Field(default=3, strict=True, ge=-1000000, le=1000000)


class TaskAccepted(BaseModel):
    task_id: str
    status_url: str


class TaskStatus(BaseModel):
    task_id: str
    state: str
    result: int | None = None
    error: str | None = None
    note: str | None = None


@app.get("/health", summary="检查 API 服务是否运行")
def health() -> dict[str, str]:
    # 存活检查不等同于 Redis 和 worker 就绪检查。
    return {"status": "ok", "service": "clipforge"}


@app.post("/demo/tasks", status_code=202, response_model=TaskAccepted,
          summary="提交后台加法任务", responses={503: {"description": "队列暂不可用"}})
def create_demo_task(payload: AddRequest, response: Response) -> TaskAccepted:
    try:
        task_id = submit_add(payload.left, payload.right)
    except QueueUnavailable as exc:
        raise HTTPException(status_code=503, detail="队列暂不可用，请检查 Redis 和服务日志。") from exc
    status_url = f"/demo/tasks/{task_id}"
    response.headers["Location"] = status_url
    return TaskAccepted(task_id=task_id, status_url=status_url)


@app.get("/demo/tasks/{task_id}", response_model=TaskStatus,
         summary="查询后台加法任务", responses={503: {"description": "结果服务暂不可用"}},
         description="查询 Celery 状态快照。PENDING 也可能表示编号未知或结果已过期；当前没有任务数据库。")
def get_demo_task(task_id: UUID) -> TaskStatus:
    try:
        return TaskStatus(**read_result(str(task_id)))
    except QueueUnavailable as exc:
        raise HTTPException(status_code=503, detail="任务结果暂不可用，请检查 Redis 和服务日志。") from exc
