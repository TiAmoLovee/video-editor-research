"""在独立客户端进程中提交任务，并等待结果；无需修改任务编号。"""

import sys

from celery.exceptions import TimeoutError
from kombu.exceptions import OperationalError

from tasks import add


def main() -> int:
    try:
        # delay 发出队列消息，由 Celery worker 计算；这里没有直接调用 add(2, 3)。
        job = add.delay(2, 3)
        print(f"task_id: {job.id}", flush=True)
        print(f"initial_state: {job.state}", flush=True)
        print("Waiting for the worker (up to 30 seconds)...", flush=True)
        value = job.get(timeout=30)
        print(f"final_state: {job.state}", flush=True)
        print(f"result: {value}", flush=True)
        if value != 5:
            raise ValueError(f"Expected 5, got {value!r}")
        print("PASS: submitted -> worker executed -> result retrieved", flush=True)
        return 0
    except TimeoutError:
        print("No result within 30 seconds. Inspect worker logs; the task may still run.", file=sys.stderr)
    except (OperationalError, OSError, ValueError) as exc:
        print(f"Queue check failed: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
