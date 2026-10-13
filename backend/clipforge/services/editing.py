"""Measured task media -> immutable EDL revisions, without rescoring or retranscribing.

Editing artifacts have their own directory. The Week 2 database, result ZIP,
download allowlist and Week 4 analysis cache remain unchanged.
"""

import argparse
from contextlib import contextmanager, ExitStack
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from clipforge.decision.scoring import validate_scored
from clipforge.editing.edl import candidates_to_edl, create_portrait_edl, frame_rate, validate_edl
from clipforge.editing.manifest import build_media_manifest, open_media_source
from clipforge.storage.jobs import get_job, job_dir, now

FILES = frozenset({"media-profile.json", "project.edl.json", "candidates.json", "analysis.json"})
DOWNLOAD_FILES = FILES | {"edit-record.json"}


class ProjectError(ValueError):
    """A safe message/status for the HTTP boundary; no private tool output."""

    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as error:
        raise ProjectError(422, "����򹤳̱����Ч��") from error


def _completed_job(task_id):
    job = get_job(task_id)
    if job is None:
        raise ProjectError(404, "���񲻴��ڡ�")
    if job["status"] != "SUCCEEDED":
        raise ProjectError(409, "��ȴ���Ƶ����ɹ���ɺ��ٱ�������嵥��")
    return job


def _contained_file(folder, relative):
    if not isinstance(relative, str) or not relative:
        raise ProjectError(409, "���������ļ���¼��������")
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder.resolve()) or not target.is_file():
        raise ProjectError(409, "���������ļ������ڻ�·����Ч��")
    return target


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _result_json(job, task_id, name):
    relative = (job.get("result") or {}).get("files", {}).get(name)
    return _read_json(_contained_file(job_dir(task_id), relative))


def _normalized_source(task_id):
    """Cache hits may reference an earlier task's media, never the raw upload."""
    seen = set()
    for _ in range(16):
        task_id = _uuid(task_id)
        if task_id in seen:
            raise ProjectError(409, "������Դ��¼����ѭ�����޷��˶Թ�һ���زġ�")
        seen.add(task_id)
        job = _completed_job(task_id)
        folder = job_dir(task_id).resolve()
        source = folder / "normalized.mp4"
        if source.exists():
            return task_id, _contained_file(folder, "normalized.mp4")
        cache = _result_json(job, task_id, "cache.json")
        if not isinstance(cache, dict) or cache.get("hit") is not True:
            raise ProjectError(409, "��һ���ز�ȱʧ������ʹ��ԭƬ���档")
        task_id = cache.get("reused_from_task_id")
    raise ProjectError(409, "������Դ���������޷��˶Թ�һ���زġ�")


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path, data):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _selected_ids(selected_ids, candidates):
    if (not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 100
            or any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)):
        raise ProjectError(422, "��ѡ�� 1�C100 �����ظ��ĺ�ѡ��ţ��������˳���ύ��")
    available = {item["id"] for item in candidates["candidates"]}
    if not set(selected_ids) <= available:
        raise ProjectError(422, "ѡ���а��������񲻴��ڵĺ�ѡ��š�")


def create_task_edl(task_id, selected_ids, *, ffprobe=None, timeout=90):
    """Verify existing task media, then publish a new revision with a commit record.

Each request is an explicit selection, not an automatic acceptance decision.
No original analysis, task result, media or previous revision is overwritten.
"""
    task_id = _uuid(task_id)
    job = _completed_job(task_id)
    try:
        candidates = _result_json(job, task_id, "candidates.json")
        analysis = _result_json(job, task_id, "analysis.json")
        validate_scored(candidates, analysis)
        _selected_ids(selected_ids, candidates)
        source_task_id, media = _normalized_source(task_id)
        manifest = build_media_manifest(media, source_id=f"normalized-{source_task_id}",
                                        ref="normalized.mp4", ffprobe=ffprobe, timeout=timeout)
        # Matching filenames or container duration are not a media identity.
        if manifest["source"]["sha256"] != analysis["media"]["normalized_sha256"]:
            raise ProjectError(409, "��һ����Ƶ�Ѹı䣬��ԭ�����ͺ�ѡ��ƥ�䡣")
        rate = frame_rate(manifest["source"]["fps"])
        frame_duration = Fraction(manifest["source"]["total_frames"], 1) / rate
        if abs(Fraction(str(analysis["media"]["duration_seconds"])) - frame_duration) > 1 / rate:
            raise ProjectError(409, "����ʱ������ʵ����Ƶ֡����һ�£����ܱ�����嵥��")
        project = candidates_to_edl(candidates, selected_ids, manifest["source"], manifest["profile"])
        if _digest(media) != manifest["source"]["sha256"]:
            raise ProjectError(409, "ý��������Ƶ�����仯��δ������嵥��")
        revision_id = str(uuid4())
        parent = job_dir(task_id).resolve() / "edits"
        if parent.resolve() != parent:
            raise ProjectError(409, "�����嵥Ŀ¼·����Ч��")
        parent.mkdir(exist_ok=True)
        target = parent / revision_id
        target.mkdir(exist_ok=False)
        published = False
        try:
            values = {"media-profile.json": manifest, "project.edl.json": project,
                      "candidates.json": candidates, "analysis.json": analysis}
            for filename, value in values.items():
                _write_json(target / filename, value)
            revision = {"format_version": 1, "task_id": task_id, "revision_id": revision_id,
                        "source_task_id": source_task_id, "created_at": now(),
                        "clip_count": len(project["clips"]), "duration_frames": project["duration_frames"],
                        "files": {name: _digest(target / name) for name in sorted(FILES)}}
            # Written last: a complete, valid commit record plus matching file
            # hashes is required by every reader. No directory rename/hardlink
            # is needed, and unfinished revisions can never be read as valid.
            _write_json(target / "revision.json", revision)
            published = True
        finally:
            if not published:
                shutil.rmtree(target)
        return revision
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "��ѡ��������ý������δͨ��У�飬δ���������嵥��") from error
    except (OSError, RuntimeError) as error:
        raise ProjectError(503, "ý�����򹤳̱���ʧ�ܣ����� FFPROBE ���ü���̨��־��") from error


def create_task_portrait(task_id, revision_id):
    """Derive a new 9:16 revision from saved selections, without reprobe/ASR.

    Keep the native media manifest and exact analysis/candidate snapshots.
    Only the new EDL's output profile/framing differs; the parent stays intact.
    """
    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    saved = load_task_edl(task_id, revision_id)
    if saved["lineage_depth"] >= 16:
        raise ProjectError(422, "����汾���Ѵﵽ 16 �㣬�뱣�����е�������ʼ�µĲ��ɰ汾��")
    parent = _revision_folder(task_id, revision_id)[2].parent
    folder, published = None, False
    try:
        with ExitStack() as stack:
            inputs = {name: stack.enter_context(open_revision_file(task_id, revision_id, name))
                      for name in sorted(FILES)}
            original = json.load(inputs['project.edl.json'])
            if original != saved['edl']:
                raise ProjectError(409, "����� EDL �ڶ�ȡ�ڼ�ı䣬δ���������汾��")
            project = create_portrait_edl(original)
            # No duplicate media copy: hold and verify the existing native source.
            media = stack.enter_context(open_revision_source(task_id, revision_id))
            new_id = str(uuid4())
            target = parent / new_id
            target.mkdir(exist_ok=False)
            folder = target
            _write_json(folder / 'project.edl.json', project)
            for name in sorted(FILES - {'project.edl.json'}):
                inputs[name].seek(0)
                with (folder / name).open('xb') as outgoing:
                    shutil.copyfileobj(inputs[name], outgoing)
                if _digest(folder / name) != saved['revision']['files'][name]:
                    raise ProjectError(409, "��Դ�����ڱ����ڼ�ı䣬δ���������汾��")
            media.seek(0)
            if hashlib.file_digest(media, 'sha256').hexdigest() != saved['manifest']['source']['sha256']:
                raise ProjectError(409, "��һ����Ƶ�ڱ����ڼ�ı䣬δ���������汾��")
            revision = {
                'format_version': 1, 'task_id': task_id, 'revision_id': new_id,
                'source_task_id': saved['revision']['source_task_id'], 'created_at': now(),
                'clip_count': len(project['clips']), 'duration_frames': project['duration_frames'],
                'files': {name: _digest(folder / name) for name in sorted(FILES)},
                'derivation': {'kind': 'center_crop_9_16', 'parent_revision_id': revision_id,
                               'parent_edl_file_sha256': saved['revision']['files']['project.edl.json']},
            }
            _write_json(folder / 'revision.json', revision)
            published = True
            return revision
    except ProjectError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "�˱���汾������������ EDL����ʹ��ԭ�����汾��") from error
    except OSError as error:
        raise ProjectError(503, "�����汾����ʧ�ܣ������̨��־��") from error
    finally:
        if folder is not None and not published:
            shutil.rmtree(folder)


def _revision_folder(task_id, revision_id):
    task_id, revision_id = _uuid(task_id), _uuid(revision_id)
    _completed_job(task_id)
    folder = job_dir(task_id).resolve() / "edits" / revision_id
    if folder.resolve() != folder or not folder.is_dir() or not (folder / "revision.json").is_file():
        raise ProjectError(404, "�����嵥�汾�����ڡ�")
    return task_id, revision_id, folder


def load_task_edl(task_id, revision_id, *, _visited=frozenset()):
    """Read the saved EDL; never reconstruct it from current rankings."""
    task_id, revision_id, folder = _revision_folder(task_id, revision_id)
    try:
        if revision_id in _visited or len(_visited) >= 16:
            raise ValueError("revision parent cycle or depth limit exceeded")
        visited = _visited | {revision_id}
        revision = _read_json(_contained_file(folder, "revision.json"))
        manual = isinstance(revision.get("derivation"), dict) and revision["derivation"].get("kind") == "manual_edit"
        expected_files = FILES | {"edit-record.json"} if manual else FILES
        if (type(revision.get("format_version")) is not int or revision["format_version"] != 1
                or revision["task_id"] != task_id or revision["revision_id"] != revision_id
                or set(revision["files"]) != expected_files):
            raise ValueError("invalid revision identity/files")
        _uuid(revision["source_task_id"])
        for filename in expected_files:
            if _digest(_contained_file(folder, filename)) != revision["files"][filename]:
                raise ValueError("changed revision artifact")
        project = _read_json(folder / "project.edl.json")
        manifest = _read_json(folder / "media-profile.json")
        validate_edl(project)
        native = manifest['source']
        native_profile = {'width': native['width'], 'height': native['height'], 'fps': native['fps']}
        if (set(manifest) != {"profile", "source"} or native_profile != manifest["profile"]
                or (project['schema_version'] == '0.1.0' and project["profile"] != manifest["profile"])
                or project["sources"] != [manifest["source"]]
                or manifest["source"]["ref"] != "normalized.mp4"
                or manifest["source"]["id"] != f"normalized-{revision['source_task_id']}"
                or revision["clip_count"] != len(project["clips"])
                or revision["duration_frames"] != project["duration_frames"]):
            raise ValueError("revision media/timeline mismatch")
        edit_record, lineage_depth = None, 1
        if manual:
            from clipforge.services.editing_changes import validate_saved_edit
            edit_record, lineage_depth = validate_saved_edit(
                task_id, revision_id, revision, project, manifest, folder, visited)
        elif project['schema_version'] == '0.2.0':
            derivation = revision['derivation']
            if (set(derivation) != {'kind', 'parent_revision_id', 'parent_edl_file_sha256'}
                    or derivation['kind'] != 'center_crop_9_16'):
                raise ValueError('invalid portrait derivation')
            parent_id = _uuid(derivation['parent_revision_id'])
            if parent_id == revision_id:
                raise ValueError('portrait parent cannot reference itself')
            parent_folder = _revision_folder(task_id, parent_id)[2]
            # Check base version before recursive load, so altered parent chains
            # cannot cause unbounded recursion or a portrait-to-portrait rewrite.
            if _read_json(_contained_file(parent_folder, 'project.edl.json'))['schema_version'] != '0.1.0':
                raise ValueError('portrait parent must be an original EDL')
            original = load_task_edl(task_id, parent_id, _visited=visited)
            lineage_depth = original["lineage_depth"] + 1
            if (original['revision']['files']['project.edl.json'] != derivation['parent_edl_file_sha256']
                    or project != create_portrait_edl(original['edl'])
                    or revision['source_task_id'] != original['revision']['source_task_id']
                    or any(revision['files'][name] != original['revision']['files'][name]
                           for name in FILES - {'project.edl.json'})):
                raise ValueError('portrait version differs from its bound parent')
            if "edit_record" in original:
                from clipforge.editing.effort import compare_edit_effort
                root_id = original["edit_record"]["baseline_revision_id"]
                baseline = load_task_edl(task_id, root_id, _visited=visited)
                edit_record = {
                    "format_version": 1, "task_id": task_id, "revision_id": revision_id,
                    "baseline_revision_id": root_id, "parent_revision_id": parent_id,
                    "editing_elapsed_ms": None, "elapsed_time_scope": "not_recorded",
                    "origin": "automatic_center_crop_no_manual_operation",
                    "effort": compare_edit_effort(baseline["edl"], project),
                    "step_effort": compare_edit_effort(original["edl"], project),
                }
        elif 'derivation' in revision:
            raise ValueError('original EDL cannot contain a portrait derivation')
        result = {"revision": revision, "edl": project, "manifest": manifest, "lineage_depth": lineage_depth}
        if edit_record is not None:
            result["edit_record"] = edit_record
        return result
    except ProjectError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProjectError(409, "����ļ����嵥����Դ��¼�Ѹı䣬�޷�ʹ�ô˰汾��") from error


def revision_file(task_id, revision_id, filename):
    if filename not in DOWNLOAD_FILES:
        raise ProjectError(404, "�˹���û�ж�Ӧ�������ļ���")
    saved = load_task_edl(task_id, revision_id)
    if filename not in saved["revision"]["files"]:
        raise ProjectError(404, "�˹���û�ж�Ӧ�������ļ���")
    return _contained_file(_revision_folder(task_id, revision_id)[2], filename)


def revision_source(task_id, revision_id):
    saved = load_task_edl(task_id, revision_id)
    source_id = saved["revision"]["source_task_id"]
    _completed_job(source_id)
    source = _contained_file(job_dir(source_id).resolve(), "normalized.mp4")
    if _digest(source) != saved["manifest"]["source"]["sha256"]:
        raise ProjectError(409, "�������õĹ�һ����Ƶ�Ѹı䣬��������Ϊԭ�زġ�")
    return source


@contextmanager
def open_revision_file(task_id, revision_id, filename):
    """Hash and download the same held handle, rather than reopening its path."""
    if filename not in DOWNLOAD_FILES:
        raise ProjectError(404, "�˹���û�ж�Ӧ�������ļ���")
    saved = load_task_edl(task_id, revision_id)
    if filename not in saved["revision"]["files"]:
        raise ProjectError(404, "�˹���û�ж�Ӧ�������ļ���")
    path = _contained_file(_revision_folder(task_id, revision_id)[2], filename)
    with open_media_source(path) as incoming:
        if hashlib.file_digest(incoming, "sha256").hexdigest() != saved["revision"]["files"][filename]:
            raise ProjectError(409, "�����ļ��Ѹı䣬�޷�ʹ�ô˰汾��")
        incoming.seek(0)
        yield incoming


@contextmanager
def open_revision_source(task_id, revision_id):
    saved = load_task_edl(task_id, revision_id)
    source_id = saved["revision"]["source_task_id"]
    _completed_job(source_id)
    path = _contained_file(job_dir(source_id).resolve(), "normalized.mp4")
    with open_media_source(path) as incoming:
        if hashlib.file_digest(incoming, "sha256").hexdigest() != saved["manifest"]["source"]["sha256"]:
            raise ProjectError(409, "�������õĹ�һ����Ƶ�Ѹı䣬��������Ϊԭ�زġ�")
        incoming.seek(0)
        yield incoming


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--selected-id", action="append")
    parser.add_argument('--portrait-from', help='derive fixed 9:16 from this saved original revision')
    parser.add_argument("--ffprobe")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if bool(args.portrait_from) == bool(args.selected_id):
        parser.error('use either --selected-id or --portrait-from')
    try:
        revision = (create_task_portrait(args.task_id, args.portrait_from) if args.portrait_from else
                    create_task_edl(args.task_id, args.selected_id, ffprobe=args.ffprobe, timeout=args.timeout))
    except ProjectError as error:
        parser.exit(1, f"STOP: {error.detail}\n")
    print(json.dumps(revision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
