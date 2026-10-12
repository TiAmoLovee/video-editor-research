"""Immutable MLT exports bound to saved EDL and optional saved subtitles.

Exports reference the verified native media; they never render or copy media,
regenerate speech recognition, or change the original revision/results.
"""

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import json
import shutil
from uuid import uuid4

from clipforge.editing.mlt import export_mlt
from clipforge.editing.manifest import open_media_source
from clipforge.services.editing import (
    ProjectError, _contained_file, _digest, _read_json, _revision_folder, _uuid,
    _write_json, load_task_edl, open_revision_file, open_revision_source,
)
from clipforge.services.subtitling import (
    _content_hash, _write_text, open_subtitle_file, open_subtitle_inputs,
)
from clipforge.storage.jobs import job_dir, now

MLT_FILES = frozenset({"project.mlt", "source.edl.json"})
SUBTITLE_EXPORT_FILES = frozenset({"subtitles.json", "captions.srt", "captions.ass"})
SUBTITLED_MLT_FILES = MLT_FILES | SUBTITLE_EXPORT_FILES


def _export_files(metadata):
    return SUBTITLED_MLT_FILES if "subtitle_id" in metadata else MLT_FILES


def _supported_project(project):
    audio_sources = {source["id"] for source in project["sources"] if source["audio_stream_index"] is not None}
    if any(clip["source_id"] in audio_sources and clip["source_out_frame"] - clip["source_in_frame"] == 1
           for clip in project["clips"]):
        raise ProjectError(409, "MLT 暂不支持含音轨的单帧片段，请使用同一 EDL 导出 MP4。")


def _saved_text_style_version(metadata):
    """Select the recorded deterministic policy without accepting ambiguous versions."""
    version = metadata['format_version']
    if type(version) is int:
        if version == 1 and 'text_style_version' not in metadata:
            return 1
        if (version == 2 and type(metadata.get('text_style_version')) is int
                and metadata['text_style_version'] == 2):
            return 2
    raise ValueError('unsupported saved MLT format/text style version')


@contextmanager
def _export_inputs(task_id, revision_id, saved, subtitle_id=None):
    """Hash and consume held source/snapshot handles throughout export."""
    with ExitStack() as stack:
        incoming = stack.enter_context(open_revision_file(task_id, revision_id, "project.edl.json"))
        project_bytes = incoming.read()
        project = json.loads(project_bytes)
        if project != saved["edl"]:
            raise ProjectError(409, "MLT 的 EDL 来源与保存的版本不一致。")
        source = stack.enter_context(open_revision_source(task_id, revision_id))
        options, subtitle_saved, subtitle_bytes = {}, None, {}
        if subtitle_id is not None:
            subtitle_saved, subtitle_project, analyses = stack.enter_context(
                open_subtitle_inputs(task_id, revision_id, subtitle_id))
            if subtitle_project != project:
                raise ProjectError(409, "MLT 与字幕必须使用同一个保存的 EDL 版本。")
            options = {"subtitle_plan": subtitle_saved["plan"], "analyses": analyses,
                       "font_name": subtitle_saved["subtitle"]["font_name"]}
            for name in SUBTITLE_EXPORT_FILES:
                stream = stack.enter_context(open_subtitle_file(task_id, revision_id, subtitle_id, name))
                subtitle_bytes[name] = stream.read()
        source_root = job_dir(saved["revision"]["source_task_id"]).resolve()
        def verify_source():
            # Detect writes on platforms without deny-write handles.
            source.seek(0)
            if hashlib.file_digest(source, "sha256").hexdigest() != project["sources"][0]["sha256"]:
                raise ProjectError(409, "MLT 引用的素材在导出期间改变，未发布工程。")
        yield project, project_bytes, source_root, options, subtitle_saved, subtitle_bytes, verify_source


def create_task_mlt_export(task_id, revision_id, *, subtitle_id=None):
    """Save a new export and completion record without mutating prior files."""
    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    subtitle_id = _uuid(subtitle_id) if subtitle_id is not None else None
    saved = load_task_edl(task_id, revision_id)
    _supported_project(saved["edl"])
    parent = _revision_folder(task_id, revision_id)[2] / "exports" / "mlt"
    if parent.resolve() != parent:
        raise ProjectError(409, "MLT 导出目录路径无效。")
    folder, published = None, False
    try:
        with _export_inputs(task_id, revision_id, saved, subtitle_id) as inputs:
            project, project_bytes, source_root, options, subtitle_saved, subtitle_bytes, verify_source = inputs
            content = export_mlt(project, source_root, text_style_version=2, **options)
            parent.mkdir(parents=True, exist_ok=True)
            export_id = str(uuid4())
            target = parent / export_id
            target.mkdir(exist_ok=False)
            folder = target  # Cleanup only a directory that this call created.
            _write_text(folder / "project.mlt", content)
            with (folder / "source.edl.json").open("xb") as output:
                output.write(project_bytes)
            for name, value in subtitle_bytes.items():
                with (folder / name).open("xb") as output:
                    output.write(value)
            metadata = {
                "format_version": 2, "text_style_version": 2, "artifact_kind": "saved_edl_mlt_export",
                "task_id": task_id, "revision_id": revision_id, "export_id": export_id,
                "created_at": now(), "source_task_id": saved["revision"]["source_task_id"],
                "edl_file_sha256": saved["revision"]["files"]["project.edl.json"],
                "edl_content_sha256": _content_hash(project), "profile": project["profile"],
                "total_frames": project["duration_frames"], "clip_count": len(project["clips"]),
                "media_sha256": project["sources"][0]["sha256"],
            }
            if "framing" in project:
                metadata["framing"] = project["framing"]
            if subtitle_saved is not None:
                metadata.update(subtitle_id=subtitle_id, subtitle_plan_sha256=_content_hash(subtitle_saved["plan"]),
                                subtitle_font_name=subtitle_saved["subtitle"]["font_name"],
                                cue_count=len(subtitle_saved["plan"]["cues"]))
            files = SUBTITLED_MLT_FILES if subtitle_id is not None else MLT_FILES
            metadata["files"] = {name: _digest(folder / name) for name in sorted(files)}
            verify_source()
            # Completion last, while verified source/snapshot handles remain held.
            _write_json(folder / "mlt-export.json", metadata)
        published = True
        return metadata
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "保存的 EDL、素材或字幕校验失败，未发布 MLT 工程。") from error
    except (OSError, RuntimeError) as error:
        raise ProjectError(503, "MLT 工程导出或保存失败，请检查后台日志。") from error
    finally:
        if folder is not None and not published:
            shutil.rmtree(folder)


def _export_folder(task_id, revision_id, export_id):
    task_id, revision_id, parent = _revision_folder(task_id, revision_id)
    export_id = _uuid(export_id)
    folder = parent / "exports" / "mlt" / export_id
    if folder.resolve() != folder or not folder.is_dir() or not (folder / "mlt-export.json").is_file():
        raise ProjectError(404, "此 EDL 版本的 MLT 导出记录不存在。")
    return task_id, revision_id, export_id, folder


def load_task_mlt_export(task_id, revision_id, export_id):
    """Verify files, identities and deterministic XML before using an export."""
    saved = load_task_edl(task_id, revision_id)
    _supported_project(saved["edl"])
    task_id, revision_id, export_id, folder = _export_folder(task_id, revision_id, export_id)
    try:
        metadata = _read_json(_contained_file(folder, "mlt-export.json"))
        text_style_version = _saved_text_style_version(metadata)
        project = saved["edl"]
        subtitle_id = None
        if "subtitle_id" in metadata:
            try:
                subtitle_id = _uuid(metadata["subtitle_id"])
            except ProjectError as error:
                raise ValueError("invalid saved MLT subtitle identifier") from error
            if subtitle_id != metadata["subtitle_id"]:
                raise ValueError("saved MLT subtitle identifier is not canonical")
        if (metadata["artifact_kind"] != "saved_edl_mlt_export"
                or metadata["task_id"] != task_id or metadata["revision_id"] != revision_id
                or metadata["export_id"] != export_id or set(metadata["files"]) != _export_files(metadata)
                or metadata["edl_file_sha256"] != saved["revision"]["files"]["project.edl.json"]
                or metadata["edl_content_sha256"] != _content_hash(project)
                or metadata["source_task_id"] != saved["revision"]["source_task_id"]
                or metadata["media_sha256"] != project["sources"][0]["sha256"]
                or metadata["profile"] != project["profile"]
                or type(metadata["total_frames"]) is not int or metadata["total_frames"] != project["duration_frames"]
                or type(metadata["clip_count"]) is not int or metadata["clip_count"] != len(project["clips"])
                or metadata.get("framing") != project.get("framing")):
            raise ValueError("MLT identity/timeline/source mismatch")
        for name in _export_files(metadata):
            if _digest(_contained_file(folder, name)) != metadata["files"][name]:
                raise ValueError("changed MLT artifact")
        with _export_inputs(task_id, revision_id, saved, subtitle_id) as inputs:
            project, project_bytes, source_root, options, subtitle_saved, subtitle_bytes, verify_source = inputs
            if ((folder / "source.edl.json").read_bytes() != project_bytes
                    or (folder / "project.mlt").read_text(encoding="utf-8")
                    != export_mlt(project, source_root, text_style_version=text_style_version, **options)):
                raise ValueError("MLT differs from its saved EDL/export policy")
            if subtitle_saved is not None:
                if (metadata["subtitle_plan_sha256"] != _content_hash(subtitle_saved["plan"])
                        or metadata["subtitle_font_name"] != subtitle_saved["subtitle"]["font_name"]
                        or type(metadata["cue_count"]) is not int or metadata["cue_count"] != len(subtitle_saved["plan"]["cues"])
                        or any((folder / name).read_bytes() != value for name, value in subtitle_bytes.items())):
                    raise ValueError("MLT subtitle snapshot identity mismatch")
            elif any(name in metadata for name in ("subtitle_plan_sha256", "subtitle_font_name", "cue_count")):
                raise ValueError("unrecorded MLT subtitle metadata")
            verify_source()
        return {"export": metadata}
    except ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "MLT 工程、素材、字幕或 EDL 来源已改变，无法使用此导出。") from error


@contextmanager
def open_mlt_export_file(task_id, revision_id, export_id, filename):
    if filename not in SUBTITLED_MLT_FILES:
        raise ProjectError(404, "此 MLT 导出没有对应的下载文件。")
    saved = load_task_mlt_export(task_id, revision_id, export_id)
    if filename not in saved["export"]["files"]:
        raise ProjectError(404, "此 MLT 导出没有对应的下载文件。")
    folder = _export_folder(task_id, revision_id, export_id)[3]
    with open_media_source(_contained_file(folder, filename)) as incoming:
        if hashlib.file_digest(incoming, "sha256").hexdigest() != saved["export"]["files"][filename]:
            raise ProjectError(409, "下载的 MLT 导出文件已改变。")
        incoming.seek(0)
        yield incoming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("revision_id")
    parser.add_argument("--subtitle-id")
    args = parser.parse_args()
    try:
        metadata = create_task_mlt_export(args.task_id, args.revision_id, subtitle_id=args.subtitle_id)
    except ProjectError as error:
        parser.exit(1, f"STOP: {error.detail}\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
