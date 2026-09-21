"""第一个 Celery 后台任务：用于验证队列连接，尚未接入视频处理。"""

import os
import time

from celery import Celery


app = Celery(
    "clipforge",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    result_expires=86400,
    task_default_queue="clipforge",
    broker_connection_retry_on_startup=True,
)


@app.task(name="clipforge.add")
def add(left: int, right: int) -> int:
    # 演示任务故意等待 3 秒，便于观察“提交后由后台执行”。
    time.sleep(3)
    return left + right
