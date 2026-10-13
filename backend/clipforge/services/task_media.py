"""Hold the normalized video bound to the task's transcript, including cache hits."""

from contextlib import contextmanager
import hashlib
from pathlib import Path

from clipforge.analysis.validation import validate_analysis
from clipforge.editing.manifest import open_media_source
from clipforge.services.editing import (
    ProjectError, _completed_job, _normalized_source, _read_json, _result_json, _uuid,
)

ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def open_task_source(task_id):
    task_id = _uuid(task_id)
    job = _completed_job(task_id)
    try:
        analysis = _result_json(job, task_id, "analysis.json")
        validate_analysis(analysis, _read_json(ROOT / "schemas/analysis.schema.json"))
        _, source = _normalized_source(task_id)
        with open_media_source(source) as incoming:
            if hashlib.file_digest(incoming, "sha256").hexdigest() != analysis["media"]["normalized_sha256"]:
                raise ProjectError(409, "视频与转写的素材来源不一致，请重新处理此任务。")
            incoming.seek(0)
            yield incoming
    except ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "归一化视频或转写记录无法核对，暂时不能播放。") from error
