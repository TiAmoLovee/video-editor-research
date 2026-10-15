"""Verified per-clip MP4 ZIPs bound to one immutable, saved EDL revision."""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
from zipfile import ZipFile, ZIP_STORED, BadZipFile

from clipforge.editing.edl import validate_edl
from clipforge.editing.manifest import _check_identity, _identity, open_media_source
from clipforge.services.editing import (
    ProjectError, _contained_file, _read_json, _revision_folder, _uuid, _write_json,
    load_task_edl, open_revision_file,
)
from clipforge.services.rendering import _content_hash
from clipforge.storage.jobs import job_dir, now

FILES = frozenset({"clips.zip", "source.edl.json", "manifest.json"})
MAX_CLIPS = 100
MAX_TOTAL_FRAMES = 18_000
MAX_PIXEL_FRAMES = 1920 * 1080 * MAX_TOTAL_FRAMES
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_HISTORY_ENTRIES = 200
CHUNK_BYTES = 1024 * 1024


def _budget(deadline):
    remaining = math.floor(deadline - time.monotonic())
    if remaining < 1:
        raise ProjectError(503, "批量导出超过限定时间，未发布结果。请先刷新本版本记录。")
    return remaining


def _hash_handle(incoming, deadline):
    incoming.seek(0)
    value = hashlib.sha256()
    while chunk := incoming.read(CHUNK_BYTES):
        _budget(deadline)
        value.update(chunk)
    _budget(deadline)
    incoming.seek(0)
    return value.hexdigest()


def _safe(path):
    if path.is_symlink() or path.resolve() != path:
        raise ProjectError(409, "批量导出目录路径无效。")
    return path


def _file(folder, filename):
    path = folder / filename
    _safe(path)
    return _contained_file(folder, filename)


def _read_bounded_json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("batch JSON exceeds the bounded size limit")
    return _read_json(path)


def _limits(project):
    count, frames = len(project["clips"]), project["duration_frames"]
    profile = project["profile"]
    if (not 1 <= count <= MAX_CLIPS or frames > MAX_TOTAL_FRAMES
            or frames * profile["width"] * profile["height"] > MAX_PIXEL_FRAMES):
        raise ProjectError(422, "批量导出限 1–100 条、总计 18000 帧，并限制总体画面工作量。请拆分保存版本。")


def _clip_project(project, clip):
    data = deepcopy(project)
    data["clips"] = [deepcopy(clip)]
    data["duration_frames"] = clip["source_out_frame"] - clip["source_in_frame"]
    data["clips"][0].update(timeline_in_frame=0, timeline_out_frame=data["duration_frames"])
    validate_edl(data)
    return data


def _clip_identity(project, clip, index):
    return {"index": index, "filename": f"clips/{index:03d}.mp4", "clip_id": clip["id"],
            "candidate_id": clip["provenance"]["candidate_id"], "source_id": clip["source_id"],
            "source_in_frame": clip["source_in_frame"], "source_out_frame": clip["source_out_frame"],
            "timeline_in_frame": clip["timeline_in_frame"], "timeline_out_frame": clip["timeline_out_frame"],
            "original_in_frame": clip["provenance"]["original_in_frame"],
            "original_out_frame": clip["provenance"]["original_out_frame"],
            "frame_count": clip["source_out_frame"] - clip["source_in_frame"]}


def _validate_report(report, project, digest):
    from clipforge.editing.render import _filter_graph
    if (report["artifact_kind"] != "edl_render_verification"
            or report["edl_sha256"] != _content_hash(project)
            or report["output"]["sha256"] != digest
            or report["output"]["profile"] != project["profile"]
            or type(report["output"]["total_frames"]) is not int
            or report["output"]["total_frames"] != project["duration_frames"]
            or report["clips"] != _filter_graph(project)[2]):
        raise ValueError("per-clip render verification differs from saved frame decisions")
    if project["schema_version"] == "0.2.0" and report.get("framing") != project["framing"]:
        raise ValueError("per-clip framing mismatch")


def _same_values(record, expected):
    return all(record[key] == value and (type(value) is not int or type(record[key]) is int)
               for key, value in expected.items())


@contextmanager
def _source(saved, deadline):
    source_task = saved["revision"]["source_task_id"]
    from clipforge.services.editing import _completed_job
    _completed_job(source_task)
    path = _file(job_dir(source_task).resolve(), "normalized.mp4")
    with open_media_source(path) as incoming:
        identity = _identity(os.fstat(incoming.fileno()))
        if _hash_handle(incoming, deadline) != saved["manifest"]["source"]["sha256"]:
            raise ProjectError(409, "归一化视频已改变，不能使用此版本的批量导出。")
        yield incoming
        _check_identity(path, identity)
        if _hash_handle(incoming, deadline) != saved["manifest"]["source"]["sha256"]:
            raise ProjectError(409, "归一化视频在操作期间改变，不能使用此批量导出。")


def _parent(task_id, revision_id):
    parent = _revision_folder(task_id, revision_id)[2] / "exports" / "batch"
    _safe(parent.parent)
    return _safe(parent)


def create_task_batch_export(task_id, revision_id, request_id, *, ffmpeg=None, ffprobe=None, timeout=90):
    """One explicit UUID request publishes at most one ZIP; never retry rendering."""
    from clipforge.editing.render import render_edl
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ProjectError(422, "超时时间必须是 1–86400 秒的整数。")
    deadline = time.monotonic() + timeout
    task_id, revision_id, export_id = _uuid(task_id), _uuid(revision_id), _uuid(request_id)
    saved = load_task_edl(task_id, revision_id)
    project = saved["edl"]
    _limits(project)
    parent = _parent(task_id, revision_id)
    folder = parent / export_id
    _safe(folder)
    if folder.exists():
        if (folder / "batch-export.json").is_file():
            return load_task_batch_export(task_id, revision_id, export_id, timeout=_budget(deadline))["export"]
        raise ProjectError(409, "此请求仍在处理或结果未确认，请刷新本版本的批量导出记录。")
    owned, published = False, False
    try:
        parent.mkdir(parents=True, exist_ok=True)
        try:
            folder.mkdir(exist_ok=False)
            owned = True
        except FileExistsError:
            raise ProjectError(409, "此请求已开始处理，请先刷新本版本的批量导出记录。") from None
        with ExitStack() as stack:
            edl_input = stack.enter_context(open_revision_file(task_id, revision_id, "project.edl.json"))
            if json.load(edl_input) != project:
                raise ProjectError(409, "保存的剪辑清单在读取期间改变，未开始批量导出。")
            media = stack.enter_context(_source(saved, deadline))
            source_root = job_dir(saved["revision"]["source_task_id"]).resolve()
            items, bytes_written = [], 0
            for index, clip in enumerate(project["clips"], start=1):
                single = _clip_project(project, clip)
                output = folder / f"{index:03d}.mp4"
                report = render_edl(single, source_root, output, ffmpeg=ffmpeg, ffprobe=ffprobe,
                                    timeout=_budget(deadline))
                with open_media_source(_file(folder, output.name)) as incoming:
                    digest = _hash_handle(incoming, deadline)
                    _validate_report(report, single, digest)
                    if Path(report["output"]["path"]).resolve() != output:
                        raise ValueError("per-clip output path mismatch")
                    size = os.fstat(incoming.fileno()).st_size
                    bytes_written += size
                    if bytes_written > MAX_ARCHIVE_BYTES:
                        raise ProjectError(422, "批量导出文件超过 512 MiB 上限，请拆分保存版本。")
                    report = deepcopy(report)
                    report["output"]["path"] = f"clips/{index:03d}.mp4"
                    items.append({**_clip_identity(project, clip, index), "bytes": size,
                                  "sha256": digest, "verification": report})
            manifest = {"format_version": 1, "artifact_kind": "edl_batch_clip_manifest", "task_id": task_id,
                        "revision_id": revision_id, "export_id": export_id, "request_id": export_id,
                        "edl_content_sha256": _content_hash(project), "profile": project["profile"],
                        "total_frames": project["duration_frames"], "clip_count": len(items),
                        "source_sha256": saved["manifest"]["source"]["sha256"], "clips": items}
            _write_json(folder / "source.edl.json", project)
            _write_json(folder / "manifest.json", manifest)
            if any((folder / name).stat().st_size > MAX_JSON_BYTES for name in ("source.edl.json", "manifest.json")):
                raise ProjectError(422, "批量清单超过 2 MiB 上限，请拆分保存版本。")
            with ZipFile(folder / "clips.zip", "x", compression=ZIP_STORED, allowZip64=False) as archive:
                for name in ("source.edl.json", "manifest.json"):
                    _add_zip_file(archive, _file(folder, name), name, deadline)
                for item in items:
                    path = _file(folder, f"{item['index']:03d}.mp4")
                    _add_zip_file(archive, path, item["filename"], deadline)
            if (folder / "clips.zip").stat().st_size > MAX_ARCHIVE_BYTES:
                raise ProjectError(422, "批量导出 ZIP 超过 512 MiB 上限，请拆分保存版本。")
            hashes = {}
            for name in sorted(FILES):
                with open_media_source(_file(folder, name)) as incoming:
                    hashes[name] = _hash_handle(incoming, deadline)
            metadata = {"format_version": 1, "artifact_kind": "edl_batch_clip_export", "task_id": task_id,
                        "revision_id": revision_id, "export_id": export_id, "request_id": export_id,
                        "created_at": now(), "edl_file_sha256": saved["revision"]["files"]["project.edl.json"],
                        "edl_content_sha256": _content_hash(project), "source_sha256": manifest["source_sha256"],
                        "profile": project["profile"], "clip_count": len(items), "total_frames": project["duration_frames"],
                        "output_bytes": (folder / "clips.zip").stat().st_size, "files": hashes}
            media.seek(0)
            if _hash_handle(media, deadline) != manifest["source_sha256"]:
                raise ProjectError(409, "归一化素材在导出期间改变，未发布批量导出。")
        # Completion is last, after all held source handles have been checked.
        _budget(deadline)
        for item in items:
            (folder / f"{item['index']:03d}.mp4").unlink()
        _write_json(folder / "batch-export.json", metadata)
        published = True
        return metadata
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError, BadZipFile) as error:
        raise ProjectError(409, "批量导出与保存的剪辑清单或来源不一致，未发布结果。") from error
    except (OSError, RuntimeError) as error:
        raise ProjectError(503, "批量渲染或打包失败，请先刷新本版本记录并检查后台日志。") from error
    finally:
        if owned and not published:
            shutil.rmtree(folder)


def _add_zip_file(archive, path, name, deadline):
    with open_media_source(path) as incoming, archive.open(name, "w") as outgoing:
        while chunk := incoming.read(CHUNK_BYTES):
            _budget(deadline)
            outgoing.write(chunk)
    _budget(deadline)


def _folder(task_id, revision_id, export_id):
    task_id, revision_id, export_id = _uuid(task_id), _uuid(revision_id), _uuid(export_id)
    folder = _safe(_parent(task_id, revision_id) / export_id)
    if not folder.is_dir() or not (folder / "batch-export.json").is_file():
        raise ProjectError(404, "此保存版本的批量导出记录不存在。")
    return task_id, revision_id, export_id, folder


def load_task_batch_export(task_id, revision_id, export_id, *, timeout=90):
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ProjectError(422, "超时时间必须是 1–86400 秒的整数。")
    deadline = time.monotonic() + timeout
    saved = load_task_edl(task_id, revision_id)
    task_id, revision_id, export_id, folder = _folder(task_id, revision_id, export_id)
    try:
        project = saved["edl"]
        _limits(project)
        metadata = _read_bounded_json(_file(folder, "batch-export.json"))
        if (type(metadata["format_version"]) is not int or metadata["format_version"] != 1
                or metadata["artifact_kind"] != "edl_batch_clip_export" or metadata["task_id"] != task_id
                or metadata["revision_id"] != revision_id or metadata["export_id"] != export_id
                or metadata["request_id"] != export_id or set(metadata["files"]) != FILES
                or metadata["edl_file_sha256"] != saved["revision"]["files"]["project.edl.json"]
                or metadata["edl_content_sha256"] != _content_hash(project)
                or metadata["source_sha256"] != saved["manifest"]["source"]["sha256"]
                or metadata["profile"] != project["profile"] or type(metadata["clip_count"]) is not int
                or metadata["clip_count"] != len(project["clips"]) or type(metadata["total_frames"]) is not int
                or metadata["total_frames"] != project["duration_frames"]
                or type(metadata["output_bytes"]) is not int
                or not 1 <= metadata["output_bytes"] <= MAX_ARCHIVE_BYTES):
            raise ValueError("batch completion identity mismatch")
        with ExitStack() as stack:
            stack.enter_context(_source(saved, deadline))
            handles = {name: stack.enter_context(open_media_source(_file(folder, name))) for name in sorted(FILES)}
            for name, incoming in handles.items():
                size = os.fstat(incoming.fileno()).st_size
                if name == "clips.zip" and size != metadata["output_bytes"]:
                    raise ValueError("batch archive size mismatch")
                if name != "clips.zip" and size > MAX_JSON_BYTES:
                    raise ValueError("batch JSON exceeds the bounded size limit")
                if _hash_handle(incoming, deadline) != metadata["files"][name]:
                    raise ValueError("changed batch artifact")
            snapshot = json.load(handles["source.edl.json"])
            manifest = json.load(handles["manifest.json"])
            if snapshot != project:
                raise ValueError("batch EDL differs from saved revision")
            expected_manifest = {"format_version": 1, "artifact_kind": "edl_batch_clip_manifest", "task_id": task_id,
                "revision_id": revision_id, "export_id": export_id, "request_id": export_id,
                "edl_content_sha256": metadata["edl_content_sha256"], "source_sha256": metadata["source_sha256"],
                "profile": project["profile"], "total_frames": project["duration_frames"], "clip_count": len(project["clips"])}
            if (set(manifest) != set(expected_manifest) | {"clips"}
                    or not _same_values(manifest, expected_manifest)
                    or not isinstance(manifest["clips"], list) or len(manifest["clips"]) != len(project["clips"])):
                raise ValueError("batch manifest differs from saved revision")
            archive = stack.enter_context(ZipFile(handles["clips.zip"]))
            names = ["source.edl.json", "manifest.json"] + [f"clips/{i:03d}.mp4" for i in range(1, len(project["clips"]) + 1)]
            entries = archive.infolist()
            if (len(entries) != len(names) or set(archive.namelist()) != set(names)
                    or any(item.compress_type != ZIP_STORED or item.flag_bits & 1 or item.file_size < 0 for item in entries)
                    or sum(item.file_size for item in entries) > MAX_ARCHIVE_BYTES):
                raise ValueError("archive whitelist/size mismatch")
            for name in ("source.edl.json", "manifest.json"):
                with archive.open(name) as incoming:
                    if _hash_handle(incoming, deadline) != metadata["files"][name]:
                        raise ValueError("archive snapshot hash mismatch")
            for index, (clip, item) in enumerate(zip(project["clips"], manifest["clips"], strict=True), start=1):
                expected = _clip_identity(project, clip, index)
                if (set(item) != set(expected) | {"bytes", "sha256", "verification"}
                        or not _same_values(item, expected)
                        or type(item["bytes"]) is not int or item["bytes"] != archive.getinfo(item["filename"]).file_size
                        or not 1 <= item["bytes"] <= MAX_ARCHIVE_BYTES):
                    raise ValueError("clip identity/range mismatch")
                with archive.open(item["filename"]) as incoming:
                    digest = _hash_handle(incoming, deadline)
                    if digest != item["sha256"]:
                        raise ValueError("archive clip hash mismatch")
                _validate_report(item["verification"], _clip_project(project, clip), digest)
                if item["verification"]["output"]["path"] != item["filename"]:
                    raise ValueError("archive clip verification path mismatch")
        return {"export": metadata, "manifest": manifest}
    except ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
        raise ProjectError(409, "批量 ZIP、清单、片段或 EDL 来源已改变，不能使用此导出。") from error


def list_task_batch_exports(task_id, revision_id, *, timeout=90):
    from clipforge.services.editing_history import _sort_key
    deadline = time.monotonic() + timeout
    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    load_task_edl(task_id, revision_id)
    parent = _parent(task_id, revision_id)
    result = {"items": [], "unavailable_count": 0}
    if not parent.exists():
        return result
    try:
        for entry_index, path in enumerate(parent.iterdir(), start=1):
            if entry_index > MAX_HISTORY_ENTRIES:
                raise ProjectError(503, "批量导出历史超过本次核验上限，请保留现有下载并检查后台。")
            _budget(deadline)
            try:
                identifier = _uuid(path.name)
                if identifier != path.name:
                    continue
            except ProjectError:
                continue
            try:
                _safe(path)
                if not path.is_dir() or not (path / "batch-export.json").exists():
                    continue
                record = load_task_batch_export(task_id, revision_id, identifier, timeout=_budget(deadline))["export"]
                _sort_key(record, "export_id")
                result["items"].append(record)
            except (ProjectError, KeyError, ValueError, TypeError):
                _budget(deadline)
                result["unavailable_count"] += 1
        result["items"].sort(key=lambda item: _sort_key(item, "export_id"), reverse=True)
        return result
    except ProjectError:
        raise
    except OSError as error:
        raise ProjectError(503, "批量导出历史暂不可读，请检查后台日志。") from error


@contextmanager
def open_batch_export_file(task_id, revision_id, export_id, filename):
    if filename not in FILES:
        raise ProjectError(404, "此批量导出没有对应的下载文件。")
    deadline = time.monotonic() + 90
    saved = load_task_batch_export(task_id, revision_id, export_id, timeout=_budget(deadline))
    folder = _folder(task_id, revision_id, export_id)[3]
    with open_media_source(_file(folder, filename)) as incoming:
        if _hash_handle(incoming, deadline) != saved["export"]["files"][filename]:
            raise ProjectError(409, "下载的批量导出文件已改变。")
        yield incoming
