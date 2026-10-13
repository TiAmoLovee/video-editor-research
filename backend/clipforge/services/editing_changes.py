"""Immutable manual EDL edits and measured changes against a bound adoption baseline."""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import shutil
from uuid import uuid4

from clipforge.editing.edl import candidates_to_edl, seconds_to_frame, validate_edl
from clipforge.editing.effort import compare_edit_effort
from clipforge.services import editing
from clipforge.storage.jobs import now

EDIT_FILE = "edit-record.json"
MAX_EDIT_DEPTH = 16
MAX_ELAPSED_MS = 86_400_000


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _requests(clips, elapsed_ms=None):
    if (not isinstance(clips, list) or not 1 <= len(clips) <= 100
            or any(not isinstance(clip, dict)
                   or set(clip) != {"candidate_id", "source_in_frame", "source_out_frame"}
                   or not isinstance(clip["candidate_id"], str)
                   or not clip["candidate_id"].strip() or len(clip["candidate_id"]) > 200
                   or type(clip["source_in_frame"]) is not int
                   or type(clip["source_out_frame"]) is not int
                   or not 0 <= clip["source_in_frame"] < clip["source_out_frame"]
                   for clip in clips)
            or len({clip["candidate_id"] for clip in clips}) != len(clips)):
        raise editing.ProjectError(422, "请选择 1–100 条不同片段，切点须为有效的整数源帧。")
    if elapsed_ms is not None and (type(elapsed_ms) is not int or not 0 <= elapsed_ms <= MAX_ELAPSED_MS):
        raise editing.ProjectError(422, "编辑经过时间无效；未计时请留空。")


def _build_project(parent, baseline, candidates, clips):
    """Only selection, source cut points and order may change; provenance is server-owned."""
    _requests(clips)
    if parent["sources"] != baseline["sources"] or len(parent["sources"]) != 1:
        raise ValueError("edit source differs from baseline")
    source = parent["sources"][0]
    if any(clip["source_out_frame"] > source["total_frames"] for clip in clips):
        raise editing.ProjectError(422, "切点超出此版本的源视频帧数。")
    available = {candidate["id"]: index for index, candidate in enumerate(candidates["candidates"])}
    selected = [clip["candidate_id"] for clip in clips]
    if not set(selected) <= available.keys():
        raise editing.ProjectError(422, "片段不属于此版本保存的候选快照。")
    native = {"width": source["width"], "height": source["height"], "fps": source["fps"]}
    imported = candidates_to_edl(candidates, selected, source, native)
    baseline_clips = {clip["provenance"]["candidate_id"]: clip for clip in baseline["clips"]}
    next_position = max(clip["provenance"]["original_selection_index"] for clip in baseline["clips"]) + 1
    project = deepcopy(parent)
    project["clips"] = imported["clips"]
    cursor = 0
    for output, request in zip(project["clips"], clips, strict=True):
        identifier = request["candidate_id"]
        if identifier in baseline_clips:
            original = baseline_clips[identifier]
            expected = deepcopy(output["provenance"])
            expected["original_selection_index"] = original["provenance"]["original_selection_index"]
            if expected != original["provenance"] or output["id"] != original["id"]:
                raise ValueError("baseline differs from saved candidate snapshot")
            output["provenance"] = deepcopy(original["provenance"])
        else:
            # Stable across edit order and subsequent deletion/reintroduction.
            output["provenance"]["original_selection_index"] = next_position + available[identifier]
        output["source_in_frame"] = request["source_in_frame"]
        output["source_out_frame"] = request["source_out_frame"]
        output["timeline_in_frame"] = cursor
        cursor += request["source_out_frame"] - request["source_in_frame"]
        output["timeline_out_frame"] = cursor
    project["duration_frames"] = cursor
    validate_edl(project)
    return project


def available_edit_clips(task_id, revision_id, saved):
    """Project precise candidate frames from this held snapshot, not current rankings."""
    with editing.open_revision_file(task_id, revision_id, "candidates.json") as incoming:
        if hashlib.file_digest(incoming, "sha256").hexdigest() != saved["revision"]["files"]["candidates.json"]:
            raise editing.ProjectError(409, "保存快照在读取期间改变，请重新打开此版本。")
        incoming.seek(0)
        candidates = json.load(incoming)["candidates"]
    if len(candidates) > 10000:
        raise editing.ProjectError(503, "保存的候选数量超过编辑投影上限。")
    source = saved["manifest"]["source"]
    available, unavailable = [], 0
    for candidate in candidates:
        start = seconds_to_frame(candidate["start"], source["fps"])
        end = seconds_to_frame(candidate["end"], source["fps"])
        if not 0 <= start < end <= source["total_frames"]:
            unavailable += 1
            continue
        available.append({"candidate_id": candidate["id"], "source_in_frame": start, "source_out_frame": end})
    return {"available_clips": available, "unavailable_edit_clip_count": unavailable}


def _record(task_id, revision_id, parent, baseline, project, clips, elapsed_ms):
    return {
        "format_version": 1,
        "task_id": task_id,
        "revision_id": revision_id,
        "parent_revision_id": parent["revision"]["revision_id"],
        "parent_edl_file_sha256": parent["revision"]["files"]["project.edl.json"],
        "baseline_revision_id": baseline["revision"]["revision_id"],
        "baseline_edl_file_sha256": baseline["revision"]["files"]["project.edl.json"],
        "requested_clips": deepcopy(clips),
        "editing_elapsed_ms": elapsed_ms,
        "elapsed_time_scope": "user_reported_session_elapsed_not_active_work" if elapsed_ms is not None else "not_recorded",
        "effort": compare_edit_effort(baseline["edl"], project),
        "step_effort": compare_edit_effort(parent["edl"], project),
    }


def create_task_edit(task_id, revision_id, clips, *, editing_elapsed_ms=None):
    """Save one explicit edit without probes, scoring, media copies or parent overwrites."""
    task_id, revision_id = editing._uuid(task_id), editing._uuid(revision_id)
    _requests(clips, editing_elapsed_ms)
    parent = editing.load_task_edl(task_id, revision_id)
    if parent["lineage_depth"] >= MAX_EDIT_DEPTH:
        raise editing.ProjectError(422, "保存版本链已达到 16 层，请保留现有导出并开始新的采纳版本。")
    root_id = parent.get("edit_record", {}).get("baseline_revision_id", revision_id)
    baseline = parent if root_id == revision_id else editing.load_task_edl(task_id, root_id)
    folder, published = None, False
    try:
        with ExitStack() as stack:
            inputs = {name: stack.enter_context(editing.open_revision_file(task_id, revision_id, name))
                      for name in sorted(editing.FILES)}
            candidates = json.load(inputs["candidates.json"])
            project = _build_project(parent["edl"], baseline["edl"], candidates, clips)
            # Holding the existing media verifies identity; it is not copied or rendered.
            media = stack.enter_context(editing.open_revision_source(task_id, revision_id))
            new_id = str(uuid4())
            folder = editing._revision_folder(task_id, revision_id)[2].parent / new_id
            folder.mkdir(exist_ok=False)
            editing._write_json(folder / "project.edl.json", project)
            for name in sorted(editing.FILES - {"project.edl.json"}):
                inputs[name].seek(0)
                with (folder / name).open("xb") as outgoing:
                    shutil.copyfileobj(inputs[name], outgoing)
                if editing._digest(folder / name) != parent["revision"]["files"][name]:
                    raise ValueError("parent snapshot changed during edit publication")
            record = _record(task_id, new_id, parent, baseline, project, clips, editing_elapsed_ms)
            editing._write_json(folder / EDIT_FILE, record)
            media.seek(0)
            if hashlib.file_digest(media, "sha256").hexdigest() != parent["manifest"]["source"]["sha256"]:
                raise ValueError("media changed during edit publication")
            files = editing.FILES | {EDIT_FILE}
            revision = {
                "format_version": 1, "task_id": task_id, "revision_id": new_id,
                "source_task_id": parent["revision"]["source_task_id"], "created_at": now(),
                "clip_count": len(project["clips"]), "duration_frames": project["duration_frames"],
                "files": {name: editing._digest(folder / name) for name in sorted(files)},
                "derivation": {"kind": "manual_edit", "parent_revision_id": revision_id,
                               "parent_edl_file_sha256": parent["revision"]["files"]["project.edl.json"],
                               "baseline_revision_id": root_id,
                               "baseline_edl_file_sha256": baseline["revision"]["files"]["project.edl.json"]},
            }
            editing._write_json(folder / "revision.json", revision)
            published = True
            return revision
    except editing.ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise editing.ProjectError(409, "来源、切点或修改记录未通过校验，未保存编辑版。") from error
    except OSError as error:
        raise editing.ProjectError(503, "编辑版保存失败，请检查后台日志。") from error
    finally:
        if folder is not None and not published:
            shutil.rmtree(folder)


def validate_saved_edit(task_id, revision_id, revision, project, manifest, folder, visited):
    """Rebuild from held baseline IDs and a checked request, never trust stored metrics."""
    derivation = revision["derivation"]
    if (set(derivation) != {"kind", "parent_revision_id", "parent_edl_file_sha256",
                           "baseline_revision_id", "baseline_edl_file_sha256"}
            or derivation["kind"] != "manual_edit"):
        raise ValueError("invalid manual edit derivation")
    parent_id = editing._uuid(derivation["parent_revision_id"])
    baseline_id = editing._uuid(derivation["baseline_revision_id"])
    parent = editing.load_task_edl(task_id, parent_id, _visited=visited)
    expected_baseline = parent.get("edit_record", {}).get("baseline_revision_id", parent_id)
    if baseline_id != expected_baseline:
        raise ValueError("manual edit cannot reset its adoption baseline")
    baseline = parent if baseline_id == parent_id else editing.load_task_edl(task_id, baseline_id, _visited=visited)
    if (parent["revision"]["files"]["project.edl.json"] != derivation["parent_edl_file_sha256"]
            or baseline["revision"]["files"]["project.edl.json"] != derivation["baseline_edl_file_sha256"]
            or parent["manifest"] != manifest
            or revision["source_task_id"] != parent["revision"]["source_task_id"]
            or any(revision["files"][name] != parent["revision"]["files"][name]
                   for name in editing.FILES - {"project.edl.json"})):
        raise ValueError("manual edit parent/baseline snapshots changed")
    record = editing._read_json(editing._contained_file(folder, EDIT_FILE))
    clips, elapsed_ms = record["requested_clips"], record["editing_elapsed_ms"]
    _requests(clips, elapsed_ms)
    candidates = editing._read_json(editing._contained_file(folder, "candidates.json"))
    expected_project = _build_project(parent["edl"], baseline["edl"], candidates, clips)
    expected_record = _record(task_id, revision_id, parent, baseline, project, clips, elapsed_ms)
    if project != expected_project or _canonical(record) != _canonical(expected_record):
        raise ValueError("edited project or measured changes differ from bound request")
    return record, parent["lineage_depth"] + 1
