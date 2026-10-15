"""Human candidate decisions and append-only audit, separate from frozen EDL schemas.

Names are supplied by the operator; they are not authenticated identities.
SQLite serializes decisions and their before/after evidence in one transaction.
Manual edit evidence lives in its immutable, hash-bound revision sidecar, so an
audit write can never leave a published edit without its corresponding evidence.
"""

from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
import sqlite3
from uuid import UUID, uuid4

from clipforge.decision.scoring import validate_scored
from clipforge.editing.manifest import open_media_source
from clipforge.services import editing
from clipforge.storage.jobs import data_root, job_dir, now

DECISIONS = frozenset({"accepted", "rejected", "undecided"})
MAX_ACTOR = 80
MAX_REASON = 2000
MAX_CANDIDATES = 10000


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize_actor(actor, *, required=True):
    if actor is None and not required:
        return None
    if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= MAX_ACTOR:
        raise editing.ProjectError(422, "请填写 1–80 字的操作者姓名；此姓名由用户填写，并非认证身份。")
    if any(ord(character) < 32 for character in actor.strip()):
        raise editing.ProjectError(422, "操作者姓名不能包含控制字符。")
    return actor.strip()


def _reason(reason):
    if reason is None:
        return None
    if not isinstance(reason, str) or len(reason.strip()) > MAX_REASON:
        raise editing.ProjectError(422, "复核备注须为不超过 2000 字的文本。")
    return reason.strip() or None


def _request_id(request_id):
    try:
        return str(UUID(str(request_id)))
    except (ValueError, TypeError, AttributeError) as error:
        raise editing.ProjectError(422, "复核请求编号须为 UUID。") from error


@contextmanager
def _database():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "reviewing.sqlite3"
    if path.resolve() != path:
        raise editing.ProjectError(409, "复核记录目录路径无效。")
    try:
        with closing(sqlite3.connect(path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("""CREATE TABLE IF NOT EXISTS review_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                task_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                event_json TEXT NOT NULL,
                UNIQUE(task_id, request_id)
            )""")
            connection.execute("""CREATE INDEX IF NOT EXISTS review_latest
                ON review_events(task_id, snapshot_sha256, candidate_id, sequence)""")
            # Application code only inserts. These guards also prevent accidental
            # future service code from rewriting or erasing the audit history.
            connection.execute("""CREATE TRIGGER IF NOT EXISTS review_no_update
                BEFORE UPDATE ON review_events BEGIN
                SELECT RAISE(ABORT, 'review audit is append-only'); END""")
            connection.execute("""CREATE TRIGGER IF NOT EXISTS review_no_delete
                BEFORE DELETE ON review_events BEGIN
                SELECT RAISE(ABORT, 'review audit is append-only'); END""")
            connection.commit()
            yield connection
    except editing.ProjectError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise editing.ProjectError(503, "复核记录暂不可用，请检查后台日志。") from error


def _candidate_snapshot(task_id):
    job = editing._completed_job(task_id)
    try:
        relative = (job.get("result") or {}).get("files", {}).get("candidates.json")
        path = editing._contained_file(job_dir(task_id), relative)
        # Hash and decode one held input, rather than hashing a path reopened later.
        with open_media_source(path) as incoming:
            snapshot_hash = hashlib.file_digest(incoming, "sha256").hexdigest()
            incoming.seek(0)
            candidates = json.load(incoming)
        analysis = editing._result_json(job, task_id, "analysis.json")
        validate_scored(candidates, analysis)
        items = candidates["candidates"]
        if len(items) > MAX_CANDIDATES:
            raise editing.ProjectError(503, "候选数量超过复核上限。")
        return snapshot_hash, [item["id"] for item in items]
    except editing.ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise editing.ProjectError(409, "当前候选或来源记录未通过校验，不能复核。") from error


def _undecided():
    return {"decision": "undecided", "actor": None, "actor_scope": "unrecorded",
            "reason": None, "created_at": None, "request_id": None}


def _event(row):
    try:
        event = json.loads(row["event_json"])
        if (type(event["format_version"]) is not int or event["format_version"] != 1
                or event["kind"] != "candidate_review"
                or event["event_id"] != row["event_id"] or event["task_id"] != row["task_id"]
                or event["request_id"] != row["request_id"]
                or event["candidate_id"] != row["candidate_id"]
                or event["candidate_snapshot_sha256"] != row["snapshot_sha256"]
                or event["decision"] not in DECISIONS
                or event["actor"] != normalize_actor(event["actor"])
                or event["actor_scope"] != "user_supplied_name"
                or event["reason"] != _reason(event["reason"])
                or event["after"] != {key: event[key] for key in _undecided()}
                or set(event["before"]) != set(_undecided())
                or event["before"]["decision"] not in DECISIONS):
            raise ValueError("invalid review event")
        if datetime.fromisoformat(event["created_at"]).tzinfo is None:
            raise ValueError("review time must include timezone")
        return event
    except (ValueError, KeyError, TypeError, editing.ProjectError) as error:
        raise editing.ProjectError(409, "复核审计记录未通过校验。") from error


def create_review(task_id, candidate_id, decision, actor, *, request_id, reason=None,
                  expected_snapshot_sha256=None):
    """Append a decision once; repeated unknown-result requests recover its event.

    Accepting does not create an EDL. Rejecting does not delete media or candidates.
    A later decision appends evidence and supersedes the state for that snapshot.
    """
    task_id = editing._uuid(task_id)
    editing._completed_job(task_id)
    actor, reason, request_id = normalize_actor(actor), _reason(reason), _request_id(request_id)
    if (not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 200
            or not isinstance(decision, str) or decision not in DECISIONS):
        raise editing.ProjectError(422, "候选编号或复核决定无效。")
    if (expected_snapshot_sha256 is not None
            and (not isinstance(expected_snapshot_sha256, str)
                 or len(expected_snapshot_sha256) != 64
                 or any(char not in "0123456789abcdef" for char in expected_snapshot_sha256))):
        raise editing.ProjectError(422, "候选快照哈希无效。")
    fingerprint = hashlib.sha256(_canonical({
        "candidate_id": candidate_id, "decision": decision, "actor": actor, "reason": reason,
        "expected_snapshot_sha256": expected_snapshot_sha256,
    }).encode("utf-8")).hexdigest()
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            previous_request = connection.execute(
                "SELECT * FROM review_events WHERE task_id=? AND request_id=?", (task_id, request_id)).fetchone()
            if previous_request is not None:
                if previous_request["request_sha256"] != fingerprint:
                    raise editing.ProjectError(409, "此请求编号已经用于另一条复核内容，请先核对原提交结果。")
                event = _event(previous_request)
                connection.commit()
                return event
            snapshot_hash, identifiers = _candidate_snapshot(task_id)
            if expected_snapshot_sha256 is not None and snapshot_hash != expected_snapshot_sha256:
                raise editing.ProjectError(409, "候选快照已改变，请刷新复核台后再提交。")
            if candidate_id not in identifiers:
                raise editing.ProjectError(422, "此任务不存在该候选编号。")
            row = connection.execute(
                "SELECT * FROM review_events WHERE task_id=? AND candidate_id=? AND snapshot_sha256=? "
                "ORDER BY sequence DESC LIMIT 1", (task_id, candidate_id, snapshot_hash)).fetchone()
            before = _event(row)["after"] if row is not None else _undecided()
            after = {"decision": decision, "actor": actor, "actor_scope": "user_supplied_name",
                     "reason": reason, "created_at": now(), "request_id": request_id}
            event = {"format_version": 1, "event_id": str(uuid4()), "kind": "candidate_review",
                     "task_id": task_id, "candidate_id": candidate_id,
                     "candidate_snapshot_sha256": snapshot_hash,
                     **after, "before": before, "after": after}
            connection.execute(
                "INSERT INTO review_events(event_id,task_id,request_id,candidate_id,snapshot_sha256,"
                "request_sha256,event_json) VALUES (?,?,?,?,?,?,?)",
                (event["event_id"], task_id, request_id, candidate_id, snapshot_hash, fingerprint, _canonical(event)))
            connection.commit()
            return event
        except BaseException:
            connection.rollback()
            raise


def review_request(task_id, request_id):
    task_id, request_id = editing._uuid(task_id), _request_id(request_id)
    editing._completed_job(task_id)
    with _database() as connection:
        row = connection.execute(
            "SELECT * FROM review_events WHERE task_id=? AND request_id=?", (task_id, request_id)).fetchone()
    if row is None:
        raise editing.ProjectError(404, "尚未找到此复核请求的已提交记录。")
    return _event(row)


def review_state(task_id):
    task_id = editing._uuid(task_id)
    snapshot_hash, identifiers = _candidate_snapshot(task_id)
    with _database() as connection:
        rows = connection.execute(
            "SELECT * FROM review_events WHERE task_id=? AND snapshot_sha256=? ORDER BY sequence",
            (task_id, snapshot_hash)).fetchall()
    latest = {}
    for row in rows:
        event = _event(row)
        latest[event["candidate_id"]] = event["after"]
    return {"format_version": 1, "task_id": task_id, "candidate_snapshot_sha256": snapshot_hash,
            "items": [{"candidate_id": identifier, **latest.get(identifier, _undecided())}
                      for identifier in identifiers]}


def clip_state(project):
    return {"duration_frames": project["duration_frames"], "clips": [
        {"candidate_id": clip["provenance"]["candidate_id"], "position": index,
         "source_in_frame": clip["source_in_frame"], "source_out_frame": clip["source_out_frame"]}
        for index, clip in enumerate(project["clips"])]}


def edit_operations(before, after):
    """Measure all boundary, insertion, deletion and surviving-order differences."""
    old = {clip["candidate_id"]: clip for clip in before["clips"]}
    new = {clip["candidate_id"]: clip for clip in after["clips"]}
    operations = []
    for identifier, clip in old.items():
        if identifier not in new:
            operations.append({"kind": "removed", "candidate_id": identifier, "before": clip, "after": None})
        elif any(clip[key] != new[identifier][key] for key in ("source_in_frame", "source_out_frame")):
            operations.append({"kind": "boundary_changed", "candidate_id": identifier,
                               "before": clip, "after": new[identifier]})
    for identifier, clip in new.items():
        if identifier not in old:
            operations.append({"kind": "added", "candidate_id": identifier, "before": None, "after": clip})
    old_order = [clip["candidate_id"] for clip in before["clips"] if clip["candidate_id"] in new]
    new_order = [clip["candidate_id"] for clip in after["clips"] if clip["candidate_id"] in old]
    if old_order != new_order:
        operations.append({"kind": "reordered", "before": old_order, "after": new_order})
    return operations


def task_audit(task_id):
    """Read decisions and fully verified immutable manual-edit evidence together."""
    task_id = editing._uuid(task_id)
    editing._completed_job(task_id)
    with _database() as connection:
        rows = connection.execute("SELECT * FROM review_events WHERE task_id=? ORDER BY sequence", (task_id,)).fetchall()
    events = [_event(row) for row in rows]
    parent = job_dir(task_id).resolve() / "edits"
    if parent.resolve() != parent:
        raise editing.ProjectError(409, "剪辑审计目录路径无效。")
    legacy_count = 0
    unrecorded_events = 0
    if parent.is_dir():
        for folder in parent.iterdir():
            try:
                revision_id = str(UUID(folder.name))
            except ValueError:
                continue
            if not (folder / "revision.json").is_file():
                continue
            saved = editing.load_task_edl(task_id, revision_id)
            record = saved.get("edit_record")
            if "derivation" not in saved["revision"]:
                actor = saved["revision"].get("actor")
                if actor is None:
                    unrecorded_events += 1
                before = {"duration_frames": 0, "clips": []}
                after = clip_state(saved["edl"])
                events.append({"format_version": 1, "event_id": revision_id, "kind": "edl_adoption",
                               "task_id": task_id, "revision_id": revision_id,
                               "actor": actor, "actor_scope": "user_supplied_name" if actor is not None else "unrecorded",
                               "created_at": saved["revision"]["created_at"],
                               "candidate_snapshot_sha256": saved["revision"]["files"]["candidates.json"],
                               "before": before, "after": after, "operations": edit_operations(before, after)})
                continue
            if (not record or saved["revision"].get("derivation", {}).get("kind") != "manual_edit"):
                continue
            old = editing.load_task_edl(task_id, record["parent_revision_id"])
            before, after = clip_state(old["edl"]), clip_state(saved["edl"])
            actor = record.get("actor")
            if actor is None:
                legacy_count += 1
                unrecorded_events += 1
            events.append({"format_version": 1, "event_id": revision_id, "kind": "manual_edit",
                           "task_id": task_id, "revision_id": revision_id,
                           "parent_revision_id": record["parent_revision_id"],
                           "actor": actor, "actor_scope": "user_supplied_name" if actor is not None else "unrecorded",
                           "created_at": saved["revision"]["created_at"],
                           "candidate_snapshot_sha256": saved["revision"]["files"]["candidates.json"],
                           "before": before, "after": after, "operations": edit_operations(before, after)})
    def timestamp(event):
        try:
            value = datetime.fromisoformat(event["created_at"])
            if value.tzinfo is None:
                raise ValueError("audit time must include timezone")
            return value
        except (ValueError, TypeError, KeyError) as error:
            raise editing.ProjectError(409, "人工操作审计时间未通过校验。") from error

    # Compare instants, including legacy revisions with different timezone offsets.
    # Review sequence remains the tie-breaker for decisions at identical timestamps.
    events.sort(key=timestamp)
    return {"format_version": 1, "task_id": task_id, "events": events,
            "unrecorded_actor_edit_count": legacy_count,
            "unrecorded_actor_event_count": unrecorded_events,
            "identity_notice": "操作者姓名由用户填写，未认证；unrecorded 表示当时未记录。"}
