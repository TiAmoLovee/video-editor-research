"""Publish EDL-derived subtitle versions from the saved analysis, offline only."""

import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from clipforge.editing.manifest import open_media_source
from clipforge.editing.subtitles import build_subtitles, to_ass, to_srt, validate_subtitles
from clipforge.services.editing import (
    ProjectError, _contained_file, _digest, _read_json, _revision_folder, _uuid,
    _write_json, load_task_edl, open_revision_file,
)
from clipforge.storage.jobs import now

SUBTITLE_FILES = frozenset({"subtitles.json", "captions.srt", "captions.ass", "source.edl.json"})
MAX_TIMING_EDITS = 100


def _timing_requests(edits, evidence_id):
    if (not isinstance(evidence_id, str) or not evidence_id.strip() or len(evidence_id) > 200
            or not isinstance(edits, list) or not 1 <= len(edits) <= MAX_TIMING_EDITS
            or any(not isinstance(edit, dict)
                   or set(edit) != {"cue_id", "timeline_in_frame", "timeline_out_frame"}
                   or not isinstance(edit["cue_id"], str) or not edit["cue_id"].strip()
                   or len(edit["cue_id"]) > 200
                   or type(edit["timeline_in_frame"]) is not int
                   or type(edit["timeline_out_frame"]) is not int
                   or not 0 <= edit["timeline_in_frame"] < edit["timeline_out_frame"] <= 2_147_483_647
                   for edit in edits)
            or len({edit["cue_id"] for edit in edits}) != len(edits)):
        raise ProjectError(422, "请提交 1–100 条不同字幕的整数帧范围，并注明人工修时依据。")


def _content_hash(value):
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_text(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(value)


@contextmanager
def _revision_inputs(task_id, revision_id, saved):
    # Keep both original snapshots locked while deriving or consuming a plan.
    with ExitStack() as stack:
        project = json.load(stack.enter_context(open_revision_file(task_id, revision_id, "project.edl.json")))
        analysis = json.load(stack.enter_context(open_revision_file(task_id, revision_id, "analysis.json")))
        if project != saved["edl"] or len(project["sources"]) != 1:
            raise ProjectError(409, "字幕的 EDL 来源与保存的版本不一致。")
        yield project, {project["sources"][0]["id"]: analysis}


def create_task_subtitles(task_id, revision_id, *, font_name=None):
    """Create a new subtitle version; original EDL/analysis/results stay intact."""
    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    saved = load_task_edl(task_id, revision_id)
    parent = _revision_folder(task_id, revision_id)[2] / "subtitles"
    if parent.resolve() != parent:
        raise ProjectError(409, "字幕目录路径无效。")
    folder, published = None, False
    try:
        with _revision_inputs(task_id, revision_id, saved) as (project, analyses):
            plan = build_subtitles(project, analyses)
            font_name = font_name or os.environ.get("SUBTITLE_FONT_NAME") or "Noto Sans CJK SC"
            srt, ass = to_srt(plan), to_ass(plan, font_name=font_name)
            parent.mkdir(exist_ok=True)
            subtitle_id = str(uuid4())
            target = parent / subtitle_id
            target.mkdir(exist_ok=False)
            folder = target  # Cleanup only a directory we successfully created.
            _write_json(folder / "subtitles.json", plan)
            _write_json(folder / "source.edl.json", project)
            _write_text(folder / "captions.srt", srt)
            _write_text(folder / "captions.ass", ass)
            metadata = {
                "format_version": 1, "task_id": task_id, "revision_id": revision_id,
                "subtitle_id": subtitle_id, "created_at": now(), "font_name": font_name,
                "edl_file_sha256": saved["revision"]["files"]["project.edl.json"],
                "analysis_file_sha256": saved["revision"]["files"]["analysis.json"],
                "plan_content_sha256": _content_hash(plan), "cue_count": len(plan["cues"]),
                "files": {name: _digest(folder / name) for name in sorted(SUBTITLE_FILES)},
            }
            _write_json(folder / "subtitle-version.json", metadata)  # Completion record last.
            published = True
            return metadata
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "保存的 EDL、转写或字幕格式校验失败，未发布字幕。") from error
    except (OSError, RuntimeError) as error:
        raise ProjectError(503, "字幕生成或保存失败，请检查后台日志。") from error
    finally:
        if folder is not None and not published:
            shutil.rmtree(folder)


def create_retimed_task_subtitles(task_id, revision_id, subtitle_id, edits, *, evidence_id):
    """Derive one timing-only version from an original plan, retaining original ASR."""
    from clipforge.editing.subtitles import retime_subtitles

    task_id, revision_id, subtitle_id = _uuid(task_id), _uuid(revision_id), _uuid(subtitle_id)
    _timing_requests(edits, evidence_id)
    saved = load_task_edl(task_id, revision_id)
    parent = _revision_folder(task_id, revision_id)[2] / "subtitles"
    if parent.resolve() != parent:
        raise ProjectError(409, "字幕目录路径无效。")
    folder, published = None, False
    try:
        with open_subtitle_inputs(task_id, revision_id, subtitle_id) as (base, project, analyses):
            if base["subtitle"]["format_version"] != 1 or base["plan"]["schema_version"] != "0.1.0":
                raise ProjectError(422, "请从原始字幕版本修时；暂不支持对已修时版本再次修改。")
            try:
                plan = retime_subtitles(base["plan"], project, analyses, edits, evidence_id=evidence_id)
            except (ValueError, KeyError, TypeError) as error:
                raise ProjectError(422, "修时范围、字幕编号或依据未通过校验，未保存新字幕。") from error
            font_name = base["subtitle"]["font_name"]
            srt, ass = to_srt(plan), to_ass(plan, font_name=font_name)
            new_id = str(uuid4())
            target = parent / new_id
            target.mkdir(exist_ok=False)
            folder = target
            _write_json(folder / "subtitles.json", plan)
            _write_json(folder / "source.edl.json", project)
            _write_text(folder / "captions.srt", srt)
            _write_text(folder / "captions.ass", ass)
            metadata = {
                "format_version": 2, "task_id": task_id, "revision_id": revision_id,
                "subtitle_id": new_id, "created_at": now(), "font_name": font_name,
                "edl_file_sha256": saved["revision"]["files"]["project.edl.json"],
                "analysis_file_sha256": saved["revision"]["files"]["analysis.json"],
                "plan_content_sha256": _content_hash(plan), "cue_count": len(plan["cues"]),
                "files": {name: _digest(folder / name) for name in sorted(SUBTITLE_FILES)},
                "timing_revision": {"parent_subtitle_id": subtitle_id, **deepcopy(plan["timing_revision"])},
            }
            _write_json(folder / "subtitle-version.json", metadata)
            published = True
            return metadata
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "原字幕、转写或 EDL 来源校验失败，未保存修时版。") from error
    except (OSError, RuntimeError) as error:
        raise ProjectError(503, "字幕修时保存失败，请检查后台日志。") from error
    finally:
        if folder is not None and not published:
            shutil.rmtree(folder)


def _subtitle_folder(task_id, revision_id, subtitle_id):
    task_id, revision_id, parent = _revision_folder(task_id, revision_id)
    subtitle_id = _uuid(subtitle_id)
    folder = parent / "subtitles" / subtitle_id
    if folder.resolve() != folder or not folder.is_dir() or not (folder / "subtitle-version.json").is_file():
        raise ProjectError(404, "此 EDL 版本的字幕记录不存在。")
    return task_id, revision_id, subtitle_id, folder


def load_task_subtitles(task_id, revision_id, subtitle_id, *, _visited=frozenset()):
    saved = load_task_edl(task_id, revision_id)
    task_id, revision_id, subtitle_id, folder = _subtitle_folder(task_id, revision_id, subtitle_id)
    try:
        if subtitle_id in _visited or len(_visited) >= 2:
            raise ValueError("subtitle parent cycle or unsupported timing chain")
        metadata = _read_json(_contained_file(folder, "subtitle-version.json"))
        if (type(metadata["format_version"]) is not int or metadata["format_version"] not in (1, 2)
                or metadata["task_id"] != task_id or metadata["revision_id"] != revision_id
                or metadata["subtitle_id"] != subtitle_id or set(metadata["files"]) != SUBTITLE_FILES
                or metadata["edl_file_sha256"] != saved["revision"]["files"]["project.edl.json"]
                or metadata["analysis_file_sha256"] != saved["revision"]["files"]["analysis.json"]):
            raise ValueError("subtitle identity/snapshot mismatch")
        for filename in SUBTITLE_FILES:
            if _digest(_contained_file(folder, filename)) != metadata["files"][filename]:
                raise ValueError("changed subtitle artifact")
        plan = _read_json(folder / "subtitles.json")
        with _revision_inputs(task_id, revision_id, saved) as (project, analyses):
            validate_subtitles(plan, project, analyses)
            if metadata["format_version"] == 1:
                if plan["schema_version"] != "0.1.0" or "timing_revision" in metadata:
                    raise ValueError("original subtitle version cannot contain a timing revision")
            else:
                from clipforge.editing.subtitles import retime_subtitles

                timing = metadata["timing_revision"]
                if (plan["schema_version"] != "0.2.0" or not isinstance(timing, dict)
                        or set(timing) != {"parent_subtitle_id", "base_plan_sha256", "evidence_id", "edits"}
                        or {key: value for key, value in timing.items() if key != "parent_subtitle_id"}
                        != plan["timing_revision"]):
                    raise ValueError("invalid timing revision metadata")
                try:
                    parent_id = _uuid(timing["parent_subtitle_id"])
                    base = load_task_subtitles(task_id, revision_id, parent_id, _visited=_visited | {subtitle_id})
                    _timing_requests(timing["edits"], timing["evidence_id"])
                except ProjectError as error:
                    raise ValueError("invalid timing parent or request") from error
                if parent_id != timing["parent_subtitle_id"]:
                    raise ValueError("subtitle parent identifier is not canonical")
                if (base["subtitle"]["format_version"] != 1 or base["plan"]["schema_version"] != "0.1.0"
                        or timing["base_plan_sha256"] != _content_hash(base["plan"])
                        or metadata["font_name"] != base["subtitle"]["font_name"]):
                    raise ValueError("timing revision differs from its original subtitle parent")
                if plan != retime_subtitles(base["plan"], project, analyses, timing["edits"], evidence_id=timing["evidence_id"]):
                    raise ValueError("timing plan differs from its parent and recorded edits")
            if (_read_json(folder / "source.edl.json") != project
                    or metadata["plan_content_sha256"] != _content_hash(plan)
                    or type(metadata["cue_count"]) is not int or metadata["cue_count"] != len(plan["cues"])
                    or (folder / "captions.srt").read_text(encoding="utf-8") != to_srt(plan)
                    or (folder / "captions.ass").read_text(encoding="utf-8") != to_ass(plan, font_name=metadata["font_name"])):
                raise ValueError("subtitle export differs from bound EDL/analysis")
        return {"subtitle": metadata, "plan": plan}
    except ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "字幕、转写或 EDL 来源已改变，无法使用此字幕版本。") from error


@contextmanager
def open_subtitle_inputs(task_id, revision_id, subtitle_id):
    """Hold the verified plan and original analysis during subtitle rendering."""
    saved = load_task_edl(task_id, revision_id)
    subtitle = load_task_subtitles(task_id, revision_id, subtitle_id)
    folder = _subtitle_folder(task_id, revision_id, subtitle_id)[3]
    with _revision_inputs(task_id, revision_id, saved) as (project, analyses):
        with open_media_source(_contained_file(folder, "subtitles.json")) as incoming:
            if hashlib.file_digest(incoming, "sha256").hexdigest() != subtitle["subtitle"]["files"]["subtitles.json"]:
                raise ProjectError(409, "字幕计划在读取期间改变，未开始烧录。")
            incoming.seek(0)
            plan = json.load(incoming)
            validate_subtitles(plan, project, analyses)
            if plan != subtitle["plan"]:
                raise ProjectError(409, "字幕计划来源不一致。")
            yield subtitle, project, analyses


@contextmanager
def open_subtitle_file(task_id, revision_id, subtitle_id, filename):
    if filename not in SUBTITLE_FILES:
        raise ProjectError(404, "此字幕版本没有对应的下载文件。")
    saved = load_task_subtitles(task_id, revision_id, subtitle_id)
    folder = _subtitle_folder(task_id, revision_id, subtitle_id)[3]
    with open_media_source(_contained_file(folder, filename)) as incoming:
        if hashlib.file_digest(incoming, "sha256").hexdigest() != saved["subtitle"]["files"][filename]:
            raise ProjectError(409, "下载的字幕文件已改变。")
        incoming.seek(0)
        yield incoming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("revision_id")
    parser.add_argument("--font-name")
    args = parser.parse_args()
    try:
        record = create_task_subtitles(args.task_id, args.revision_id, font_name=args.font_name)
    except ProjectError as error:
        parser.exit(1, f"STOP: {error.detail}\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
