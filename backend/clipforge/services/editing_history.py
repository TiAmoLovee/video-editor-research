"""Read bounded histories of completed, verified editing artifacts.

History is discovery, not a new publication path. Every returned item is checked
by its existing reader; unfinished directories, raw paths and corrupt records
are never exposed. No media probing, rendering or model calls are performed.
"""

from datetime import datetime, timezone
from uuid import UUID

from clipforge.services.editing import ProjectError, _completed_job, _revision_folder, _uuid, load_task_edl
from clipforge.storage.jobs import job_dir

MAX_DIRECTORY_ENTRIES = 500
MAX_COMPLETED_RECORDS = 100
MAX_PAGE_SIZE = 50
REVISION_FIELDS = frozenset({"format_version", "task_id", "revision_id", "source_task_id", "created_at",
                             "clip_count", "duration_frames", "files", "derivation"})
ARTIFACT_FIELDS = {
    "subtitle": frozenset({"format_version", "task_id", "revision_id", "subtitle_id", "created_at", "font_name",
                            "edl_file_sha256", "analysis_file_sha256", "plan_content_sha256", "cue_count", "files"}),
    "render": frozenset({"format_version", "task_id", "revision_id", "render_id", "created_at", "edl_file_sha256",
                          "edl_content_sha256", "profile", "total_frames", "output_bytes", "files", "subtitle_id",
                          "subtitle_plan_sha256", "subtitle_font_name"}),
    "export": frozenset({"format_version", "text_style_version", "artifact_kind", "task_id", "revision_id", "export_id", "created_at",
                          "source_task_id", "edl_file_sha256", "edl_content_sha256", "profile", "total_frames",
                          "clip_count", "media_sha256", "framing", "subtitle_id", "subtitle_plan_sha256",
                          "subtitle_font_name", "cue_count", "files"}),
}


class _Budget:
    def __init__(self):
        self.entries = 0
        self.completed = 0

    def consume(self, *, completed=False):
        if completed:
            self.completed += 1
        else:
            self.entries += 1
        if self.entries > MAX_DIRECTORY_ENTRIES or self.completed > MAX_COMPLETED_RECORDS:
            raise ProjectError(503, "历史记录超过本次核验上限，请保留现有下载记录并检查后台日志。")


def _safe_folder(path):
    if path.is_symlink() or path.resolve() != path:
        raise ProjectError(409, "历史记录目录路径无效。")


def _completed_folders(parent, marker, budget):
    """Yield safe UUID directories, plus rejected paths without following them."""
    _safe_folder(parent)
    if not parent.exists():
        return
    if not parent.is_dir():
        raise ProjectError(409, "历史记录目录路径无效。")
    for child in parent.iterdir():
        budget.consume()
        try:
            identifier = str(UUID(child.name))
        except (ValueError, AttributeError):
            continue
        if identifier != child.name:
            continue
        # Never inspect a completion record through a symlink or junction.
        if child.is_symlink() or child.resolve() != child:
            yield identifier, False
            continue
        if not child.is_dir():
            continue
        completion = child / marker
        if completion.is_symlink() or completion.resolve() != completion:
            yield identifier, False
        elif completion.exists():
            budget.consume(completed=True)
            yield identifier, completion.is_file()


def _sort_key(record, identifier_field):
    value = record["created_at"]
    if not isinstance(value, str):
        raise ValueError("invalid history timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("history timestamp must include timezone")
    return stamp.astimezone(timezone.utc), record[identifier_field]


def _pagination(limit, offset):
    if (type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE
            or type(offset) is not int or not 0 <= offset <= MAX_DIRECTORY_ENTRIES):
        raise ProjectError(422, "分页参数无效：每页须为 1–50 条，起始位置须为 0–500。")


def list_task_edls(task_id, *, limit=10, offset=0):
    """Return verified revisions with the exact saved selection order."""
    _pagination(limit, offset)
    task_id = _uuid(task_id)
    _completed_job(task_id)
    items, unavailable, budget = [], 0, _Budget()
    try:
        parent = job_dir(task_id).resolve() / "edits"
        for revision_id, safe in _completed_folders(parent, "revision.json", budget):
            if not safe:
                unavailable += 1
                continue
            try:
                saved = load_task_edl(task_id, revision_id)
                record = {key: value for key, value in saved["revision"].items() if key in REVISION_FIELDS}
                item = {**record, "profile": saved["edl"]["profile"],
                        "selected_ids": [clip["provenance"]["candidate_id"] for clip in saved["edl"]["clips"]]}
                _sort_key(item, "revision_id")
                items.append(item)
            except (ProjectError, ValueError, KeyError, TypeError):
                unavailable += 1
        items.sort(key=lambda item: _sort_key(item, "revision_id"), reverse=True)
        total = len(items)
        return {"items": items[offset:offset + limit], "total": total, "offset": offset, "limit": limit,
                "has_more": offset + limit < total, "unavailable_count": unavailable}
    except ProjectError:
        raise
    except OSError as error:
        raise ProjectError(503, "历史版本暂不可读，请检查后台日志。") from error


def list_revision_artifacts(task_id, revision_id):
    """Discover completed artifacts belonging only to this verified revision."""
    # Lazy imports keep history independent of the existing publication graph.
    from clipforge.services.mlt_exporting import load_task_mlt_export
    from clipforge.services.rendering import load_task_render
    from clipforge.services.subtitling import load_task_subtitles

    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    load_task_edl(task_id, revision_id)
    folder = _revision_folder(task_id, revision_id)[2]
    specs = (
        ("subtitles", folder / "subtitles", "subtitle-version.json", load_task_subtitles, "subtitle", "subtitle_id"),
        ("renders", folder / "renders", "render.json", load_task_render, "render", "render_id"),
        ("mlt_exports", folder / "exports" / "mlt", "mlt-export.json", load_task_mlt_export, "export", "export_id"),
    )
    result = {"subtitles": [], "renders": [], "mlt_exports": [], "unavailable_count": 0}
    budget = _Budget()
    try:
        for key, parent, marker, reader, wrapper, id_field in specs:
            for identifier, safe in _completed_folders(parent, marker, budget):
                if not safe:
                    result["unavailable_count"] += 1
                    continue
                try:
                    saved_record = reader(task_id, revision_id, identifier)[wrapper]
                    record = {key: value for key, value in saved_record.items() if key in ARTIFACT_FIELDS[wrapper]}
                    _sort_key(record, id_field)
                    result[key].append(record)
                except (ProjectError, ValueError, KeyError, TypeError):
                    result["unavailable_count"] += 1
            result[key].sort(key=lambda record: _sort_key(record, id_field), reverse=True)
        return result
    except ProjectError:
        raise
    except OSError as error:
        raise ProjectError(503, "历史导出暂不可读，请检查后台日志。") from error
