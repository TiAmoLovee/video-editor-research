"""Check a real local API's invalid uploads, failed downloads, and subsequent valid task.

Creates one deliberate failed task (unless --failed-task-id is provided) and one
new valid task. Requires running API/Redis/worker and a small valid video fixture.
"""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8200')
    parser.add_argument('--recovery-video', type=Path, required=True)
    parser.add_argument('--failed-task-id', type=UUID)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--output', type=Path, default=Path('downloads/failure_verification.json'))
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.path not in {'', '/'} or url.query or url.fragment:
        parser.error('base-url must be an HTTP(S) origin')
    if not args.recovery_video.is_file() or not 0 < args.recovery_video.stat().st_size <= 10 * 1024 * 1024:
        parser.error('recovery-video must be a valid small video, at most 10 MiB')
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    base = args.base_url.rstrip('/')
    opener = build_opener(ProxyHandler({}))

    def request(path, data=None, headers=None):
        try:
            response = opener.open(Request(base + path, data=data, headers=headers or {}), timeout=30)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read()

    def get_json(path):
        code, raw = request(path)
        if code != 200:
            raise ValueError(f'GET {path}: HTTP {code}')
        return json.loads(raw)

    def upload(name, content):
        name = name.replace('"', '_').replace('\r', '_').replace('\n', '_')
        boundary = 'clipforge' + uuid4().hex
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode('utf-8')
        body = head + content + f'\r\n--{boundary}--\r\n'.encode('ascii')
        code, raw = request('/tasks', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})
        return code, json.loads(raw)

    def wait_task(task_id):
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            job = get_json(f'/tasks/{task_id}')
            if job['status'] in {'SUCCEEDED', 'FAILED', 'SUBMISSION_UNKNOWN'}:
                return job
            time.sleep(0.5)
        raise ValueError(f'Timed out; preserve task {task_id}, do not blindly upload again')

    code, _ = upload('intentional_invalid_extension.txt', b'not a video')
    assert code == 415, code
    code, _ = upload('intentional_empty_sample.mp4', b'')
    assert code == 422, code
    print('PASS: invalid extension -> 415; empty upload -> 422', flush=True)
    if args.failed_task_id:
        failed_id = str(args.failed_task_id)
    else:
        code, response = upload('intentional_corrupt_sample.mp4', b'ClipForge deliberate invalid-video fixture.')
        assert code == 202, response
        failed_id = response['task_id']
    print('failed_task_id:', failed_id, flush=True)
    failed = wait_task(failed_id)
    assert failed['status'] == 'FAILED' and failed['stage'] == 'probing', failed
    assert failed['result'] is None and failed['error'], failed
    denied = {}
    for name in ['result.zip', 'clip_001.mp4']:
        code, _ = request(f'/tasks/{failed_id}/files/{name}')
        assert code == 409, (name, code)
        denied[name] = code
    print('PASS: corrupt video -> FAILED; ZIP and MP4 downloads -> 409', flush=True)

    code, response = upload(args.recovery_video.name, args.recovery_video.read_bytes())
    assert code == 202, response
    recovery_id = response['task_id']
    print('recovery_task_id:', recovery_id, flush=True)
    recovery = wait_task(recovery_id)
    assert recovery['status'] == 'SUCCEEDED', recovery
    code, raw = request(f'/tasks/{recovery_id}/files/result.zip')
    assert code == 200
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None
        plan = json.loads(archive.read('clip_plan.json'))
        names = {'media_meta.json', 'normalized_media_meta.json', 'clip_plan.json'}
        names.update(c['file'] for c in plan['clips'])
        assert len(archive.namelist()) == len(names) and set(archive.namelist()) == names
        assert len(plan['clips']) == recovery['result']['clip_count'] > 0
        assert sum(c['frame_count'] for c in plan['clips']) == plan['total_frames']
    assert get_json('/health')['status'] == 'ok'
    assert get_json(f'/tasks/{failed_id}')['status'] == 'FAILED'
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'passed': True,
              'invalid_extension_http_status': 415, 'empty_upload_http_status': 422,
              'failed_task': {k: failed[k] for k in ['task_id', 'source_name', 'status', 'stage', 'error', 'result']},
              'failed_download_http_status': denied,
              'recovery_task': {k: recovery[k] for k in ['task_id', 'source_name', 'status', 'stage']},
              'recovery_clip_count': len(plan['clips']), 'recovery_frame_count_from_plan': plan['total_frames'],
              'recovery_zip_crc_and_manifest_passed': True, 'health_after_failure': 'ok',
              'limits': ['This verifies handling a failed input, not recovery from an interrupted worker.',
                         'Does not test >1 GiB uploads, full disk, resource exhaustion or mid-encode failure.',
                         'Browser behavior and decoded frames need separate verification.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('PASS: valid task after failure -> SUCCEEDED -> ZIP verified', flush=True)
    print('report:', args.output.resolve(), flush=True)


if __name__ == '__main__':
    main()
