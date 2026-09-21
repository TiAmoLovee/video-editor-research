"""单机任务记录。API 和 worker 使用同一个本地数据卷与 SQLite 数据库。"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID


def data_root() -> Path:
    return Path(os.environ.get("CLIPFORGE_DATA_DIR", "data")).resolve()


def job_dir(task_id: str) -> Path:
    # 数据目录只由规范 UUID 定位，不拼接用户传来的路径或文件名。
    return data_root() / "jobs" / str(UUID(task_id))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "tasks.sqlite3", timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
                task_id TEXT PRIMARY KEY, source_name TEXT NOT NULL,
                source_key TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                progress INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, error TEXT, result_json TEXT
            )""")
            yield connection


def create_job(task_id: str, source_name: str, source_key: str) -> None:
    stamp = now()
    with database() as connection:
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, 'QUEUED', 'queued', 0, ?, ?, NULL, NULL)",
            (str(UUID(task_id)), source_name, source_key, stamp, stamp),
        )


def get_job(task_id: str) -> dict | None:
    with database() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE task_id = ?", (str(UUID(task_id)),)).fetchone()
    if row is None:
        return None
    result = dict(row)
    encoded = result.pop("result_json")
    result["result"] = json.loads(encoded) if encoded else None
    return result


def claim_job(task_id: str) -> bool:
    # 原子抢占，重复投递不会再次转码或覆盖成品。
    with database() as connection:
        return connection.execute(
            "UPDATE jobs SET status='RUNNING', stage='probing', progress=10, error=NULL, updated_at=? "
            "WHERE task_id=? AND status IN ('QUEUED', 'SUBMISSION_UNKNOWN')",
            (now(), str(UUID(task_id))),
        ).rowcount == 1


def submission_unknown(task_id: str) -> None:
    with database() as connection:
        connection.execute(
            "UPDATE jobs SET status='SUBMISSION_UNKNOWN', error=?, updated_at=? "
            "WHERE task_id=? AND status='QUEUED'",
            ("队列提交未获确认；请查询状态并检查 worker 日志，避免重复上传。", now(), task_id),
        )


def update_job(task_id: str, status: str, stage: str, progress: int,
               error: str | None = None, result: dict | None = None) -> None:
    if not 0 <= progress <= 100:
        raise ValueError("Progress must be between 0 and 100")
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False) if result is not None else None
    with database() as connection:
        connection.execute(
            "UPDATE jobs SET status=?, stage=?, progress=?, error=?, result_json=?, updated_at=? "
            "WHERE task_id=? AND status='RUNNING'",
            (status, stage, progress, error, encoded, now(), task_id),
        )
