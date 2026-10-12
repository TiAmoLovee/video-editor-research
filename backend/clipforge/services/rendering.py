"""Render a saved EDL revision, without rebuilding selections or altering old results."""

import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from clipforge.editing.edl import validate_edl
from clipforge.editing.manifest import open_media_source
from clipforge.services.editing import (
    ProjectError, _contained_file, _digest, _read_json, _revision_folder, _uuid, _write_json,
    load_task_edl, open_revision_file,
)
from clipforge.storage.jobs import job_dir, now

RENDER_FILES = frozenset({"output.mp4", "verification.json", "source.edl.json"})
SUBTITLED_RENDER_FILES = RENDER_FILES | {"subtitles.json", "captions.srt", "captions.ass"}


def _render_files(metadata):
    return SUBTITLED_RENDER_FILES if "subtitle_id" in metadata else RENDER_FILES


def _content_hash(value):
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _budget(deadline):
    remaining = math.floor(deadline - time.monotonic())
    if remaining < 1:
        raise ProjectError(503, "成片处理超过限定时间，未发布结果。")
    return remaining


def _hash_handle(incoming, deadline):
    value = hashlib.sha256()
    while chunk := incoming.read(1024 * 1024):
        _budget(deadline)
        value.update(chunk)
    _budget(deadline)
    return value.hexdigest()


def create_task_render(task_id, revision_id, *, ffmpeg=None, ffprobe=None, timeout=90,
                       subtitle_id=None, font_file=None, font_name=None):
    """Publish a new render with a completion record; preserve its EDL revision."""
    from clipforge.editing.render import render_edl

    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ProjectError(422, "超时时间必须是 1–86400 秒的整数。")
    deadline = time.monotonic() + timeout
    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    saved = load_task_edl(task_id, revision_id)
    parent = _revision_folder(task_id, revision_id)[2] / "renders"
    if parent.resolve() != parent:
        raise ProjectError(409, "成片目录路径无效。")
    folder = None
    published = False
    try:
        # Hold the verified EDL file itself, rather than merely remembering its
        # path and reopening it after a potentially long render.
        with ExitStack() as stack:
            incoming = stack.enter_context(open_revision_file(task_id, revision_id, "project.edl.json"))
            project = json.load(incoming)
            validate_edl(project)
            if project != saved["edl"]:
                raise ProjectError(409, "剪辑清单在读取期间改变，未开始渲染。")
            input_hash = _content_hash(project)
            subtitle_options, subtitle_saved = {}, None
            if subtitle_id is not None:
                from clipforge.services.subtitling import open_subtitle_inputs

                subtitle_saved, subtitle_project, analyses = stack.enter_context(
                    open_subtitle_inputs(task_id, revision_id, subtitle_id))
                if subtitle_project != project:
                    raise ProjectError(409, "字幕与成片必须使用同一个保存的 EDL 版本。")
                subtitle_options = {"subtitle_plan": subtitle_saved["plan"], "analyses": analyses,
                                    "font_file": font_file, "font_name": font_name}
            elif font_file is not None or font_name is not None:
                raise ProjectError(422, "请先指定已保存的字幕版本。")
            parent.mkdir(exist_ok=True)
            render_id = str(uuid4())
            target = parent / render_id
            target.mkdir(exist_ok=False)
            folder = target
            output = folder / "output.mp4"
            source_root = job_dir(saved["revision"]["source_task_id"]).resolve()
            report = render_edl(project, source_root, output, ffmpeg=ffmpeg, ffprobe=ffprobe,
                                timeout=_budget(deadline), **subtitle_options)
            with open_media_source(_contained_file(folder, "output.mp4")) as verified:
                output_hash = _hash_handle(verified, deadline)
                actual = report["output"]
                if (report["artifact_kind"] != "edl_render_verification"
                        or report["edl_sha256"] != input_hash
                        or actual["sha256"] != output_hash
                        or Path(actual["path"]).resolve() != output.resolve()
                        or actual["total_frames"] != project["duration_frames"]
                        or actual["profile"] != project["profile"]):
                    raise ProjectError(409, "渲染结果与保存的 EDL 或实文件不一致，未发布成片。")
                if project['schema_version'] == '0.2.0' and report.get('framing') != project['framing']:
                    raise ProjectError(409, "成片裁剪配置与保存的 EDL 不一致，未发布成片。")
                report = deepcopy(report)
                report["output"]["path"] = "output.mp4"
                subtitle_metadata, files = {}, RENDER_FILES
                if subtitle_saved is not None:
                    from clipforge.editing.subtitles import to_ass, to_srt
                    from clipforge.services.subtitling import _write_text

                    plan = subtitle_saved["plan"]
                    subtitle_facts = report["subtitles"]
                    ass = to_ass(plan, font_name=subtitle_facts["font_name"])
                    if (subtitle_facts["plan_sha256"] != _content_hash(plan)
                            or subtitle_facts["ass_sha256"] != hashlib.sha256(ass.encode("utf-8")).hexdigest()):
                        raise ProjectError(409, "烧录字幕与保存的字幕计划不一致，未发布成片。")
                    _write_json(folder / "subtitles.json", plan)
                    _write_text(folder / "captions.srt", to_srt(plan))
                    _write_text(folder / "captions.ass", ass)
                    subtitle_metadata = {"subtitle_id": subtitle_saved["subtitle"]["subtitle_id"],
                                         "subtitle_plan_sha256": _content_hash(plan),
                                         "subtitle_font_name": subtitle_facts["font_name"]}
                    files = SUBTITLED_RENDER_FILES
                _write_json(folder / "source.edl.json", project)
                _write_json(folder / "verification.json", report)
                metadata = {
                    "format_version": 1, "task_id": task_id, "revision_id": revision_id,
                    "render_id": render_id, "created_at": now(),
                    "edl_file_sha256": saved["revision"]["files"]["project.edl.json"],
                    "edl_content_sha256": input_hash, "profile": project["profile"],
                    "total_frames": project["duration_frames"], "output_bytes": os.fstat(verified.fileno()).st_size,
                    "files": {name: output_hash if name == "output.mp4" else _digest(folder / name)
                              for name in sorted(files)}, **subtitle_metadata,
                }
                _budget(deadline)
                _write_json(folder / "render.json", metadata)
            published = True
            return metadata
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "保存的 EDL、素材或成片校验失败，未发布结果。") from error
    except (OSError, RuntimeError) as error:
        raise ProjectError(503, "成片渲染失败，请检查 FFmpeg/FFprobe 配置及后台日志。") from error
    finally:
        if folder is not None and not published:
            shutil.rmtree(folder)


def _render_folder(task_id, revision_id, render_id):
    task_id, revision_id, folder = _revision_folder(task_id, revision_id)
    render_id = _uuid(render_id)
    folder = folder / "renders" / render_id
    if folder.resolve() != folder or not folder.is_dir() or not (folder / "render.json").is_file():
        raise ProjectError(404, "此 EDL 版本的成片记录不存在。")
    return task_id, revision_id, render_id, folder


def load_task_render(task_id, revision_id, render_id):
    saved = load_task_edl(task_id, revision_id)
    task_id, revision_id, render_id, folder = _render_folder(task_id, revision_id, render_id)
    try:
        metadata = _read_json(_contained_file(folder, "render.json"))
        if (type(metadata["format_version"]) is not int or metadata["format_version"] != 1
                or metadata["task_id"] != task_id or metadata["revision_id"] != revision_id
                or metadata["render_id"] != render_id or set(metadata["files"]) != _render_files(metadata)
                or metadata["edl_file_sha256"] != saved["revision"]["files"]["project.edl.json"]):
            raise ValueError("render identity/completion record mismatch")
        for filename in _render_files(metadata):
            if _digest(_contained_file(folder, filename)) != metadata["files"][filename]:
                raise ValueError("changed render artifact")
        snapshot = _read_json(folder / "source.edl.json")
        report = _read_json(folder / "verification.json")
        validate_edl(snapshot)
        if (snapshot != saved["edl"] or _content_hash(snapshot) != metadata["edl_content_sha256"]
                or metadata["profile"] != snapshot["profile"]
                or metadata["total_frames"] != snapshot["duration_frames"]
                or type(metadata["output_bytes"]) is not int
                or metadata["output_bytes"] != (folder / "output.mp4").stat().st_size
                or report["artifact_kind"] != "edl_render_verification"
                or report["edl_sha256"] != metadata["edl_content_sha256"]
                or report["output"]["sha256"] != metadata["files"]["output.mp4"]
                or report["output"]["total_frames"] != metadata["total_frames"]
                or report["output"]["profile"] != metadata["profile"]):
            raise ValueError("render verification does not match its EDL/snapshot/output")
        if snapshot['schema_version'] == '0.2.0' and report.get('framing') != snapshot['framing']:
            raise ValueError('render framing differs from the saved EDL')
        if "subtitle_id" in metadata:
            from clipforge.editing.subtitles import to_ass, to_srt
            from clipforge.services.subtitling import load_task_subtitles

            subtitle = load_task_subtitles(task_id, revision_id, metadata["subtitle_id"])
            plan = _read_json(folder / "subtitles.json")
            ass = to_ass(plan, font_name=metadata["subtitle_font_name"])
            if (plan != subtitle["plan"] or _content_hash(plan) != metadata["subtitle_plan_sha256"]
                    or report["subtitles"]["plan_sha256"] != metadata["subtitle_plan_sha256"]
                    or report["subtitles"]["font_name"] != metadata["subtitle_font_name"]
                    or report["subtitles"]["ass_sha256"] != hashlib.sha256(ass.encode("utf-8")).hexdigest()
                    or (folder / "captions.ass").read_text(encoding="utf-8") != ass
                    or (folder / "captions.srt").read_text(encoding="utf-8") != to_srt(plan)):
                raise ValueError("rendered subtitles do not match their saved plan/analysis")
        elif "subtitles" in report:
            raise ValueError("unrecorded subtitle render")
        return {"render": metadata, "verification": report}
    except ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "成片、核验报告或 EDL 来源已改变，无法使用此成片。") from error


@contextmanager
def open_render_file(task_id, revision_id, render_id, filename):
    if filename not in SUBTITLED_RENDER_FILES:
        raise ProjectError(404, "此成片没有对应的下载文件。")
    saved = load_task_render(task_id, revision_id, render_id)
    if filename not in saved["render"]["files"]:
        raise ProjectError(404, "此成片没有对应的下载文件。")
    folder = _render_folder(task_id, revision_id, render_id)[3]
    with open_media_source(_contained_file(folder, filename)) as incoming:
        if hashlib.file_digest(incoming, "sha256").hexdigest() != saved["render"]["files"][filename]:
            raise ProjectError(409, "下载的成片文件已改变。")
        incoming.seek(0)
        yield incoming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("revision_id")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--subtitle-id")
    parser.add_argument("--font-file", type=Path)
    parser.add_argument("--font-name")
    args = parser.parse_args()
    try:
        report = create_task_render(args.task_id, args.revision_id, ffmpeg=args.ffmpeg,
                                    ffprobe=args.ffprobe, timeout=args.timeout,
                                    subtitle_id=args.subtitle_id, font_file=args.font_file, font_name=args.font_name)
    except ProjectError as error:
        parser.exit(1, f"STOP: {error.detail}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
