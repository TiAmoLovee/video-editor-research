"""HTTP 接口使用的队列客户端；这里不执行任务，也不等待任务完成。"""


class QueueUnavailable(Exception):
    """提交或读取队列失败，由 HTTP 层转换为 503。"""


def submit_video(task_id: str) -> None:
    from kombu.exceptions import OperationalError
    from redis.exceptions import RedisError
    from clipforge.queue.tasks import video_task

    try:
        video_task.apply_async(args=[task_id], task_id=task_id, retry=False)
    except (OperationalError, RedisError, OSError) as exc:
        raise QueueUnavailable("Video submission failed") from exc


def submit_add(left: int, right: int) -> str:
    from kombu.exceptions import OperationalError
    from redis.exceptions import RedisError
    from clipforge.queue.tasks import add

    try:
        return add.apply_async(args=[left, right], retry=False).id
    except (OperationalError, RedisError, OSError) as exc:
        raise QueueUnavailable("Queue submission failed") from exc


def read_result(task_id: str) -> dict:
    from kombu.exceptions import OperationalError
    from redis.exceptions import RedisError
    from clipforge.queue.tasks import app

    try:
        # 一次读取状态快照；不使用会等待任务结束的 result.get()。
        meta = app.backend.get_task_meta(task_id)
        state = meta["status"]
        return {
            "task_id": task_id,
            "state": state,
            "result": meta.get("result") if state == "SUCCESS" else None,
            "error": "后台任务执行失败，请检查 worker 日志。" if state == "FAILURE" else None,
            "note": "PENDING 表示尚无状态记录，也可能是编号不存在或结果已过期。" if state == "PENDING" else None,
        }
    except (OperationalError, RedisError, OSError) as exc:
        raise QueueUnavailable("Queue lookup failed") from exc
