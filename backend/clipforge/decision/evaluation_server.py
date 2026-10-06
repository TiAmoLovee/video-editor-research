"""Loopback-only human evaluation UI with versioned local review records."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from clipforge.decision.candidates import content_hash
from clipforge.decision.evaluation import read, summary, validate_answers, VERSION
from clipforge.decision.reviewed import file_hash


def create_app(folder, port=8307):
    root = Path(folder).resolve()
    manifest = read(root/'manifest.json')
    plan = {k: v for k, v in manifest.items() if k not in ('batch_id', 'renders', 'prepared_at')}
    if manifest['version'] != VERSION or content_hash(plan) != manifest['batch_id']:
        raise ValueError('评测清单已变化，请重新准备独立批次')
    by_id = {s['id']: s for s in manifest['samples']}
    if len(by_id) != manifest['denominator'] or set(manifest['renders']) != set(by_id):
        raise ValueError('试听文件清单不完整')
    for sid, entry in manifest['renders'].items():
        target = (root/entry['file']).resolve()
        if (not target.is_relative_to(root) or entry['file'] != by_id[sid]['file']
                or file_hash(target) != entry['sha256']):
            raise ValueError('试听文件与冻结的评测清单不匹配')
    manifest_hash = content_hash(manifest)
    history = root/'reviews'
    history.mkdir(exist_ok=True)
    if not history.resolve().is_relative_to(root):
        raise ValueError('评审记录目录异常')
    lock = Lock()

    def state():
        records = sorted(history.glob('review-[0-9][0-9][0-9][0-9][0-9][0-9].json'))
        result = {'batch_id': manifest['batch_id'], 'manifest_sha256': manifest_hash, 'revision': 0, 'reviews': {}}
        if records:
            if not records[-1].resolve().is_relative_to(root):
                raise ValueError('评审记录路径异常')
            result = read(records[-1])
            if (result['batch_id'] != manifest['batch_id'] or result['manifest_sha256'] != manifest_hash
                    or type(result['revision']) is not int or result['revision'] < 1
                    or records[-1].name != f"review-{result['revision']:06}.json"):
                raise ValueError('评审记录与试听批次不匹配')
        result['summary'] = summary(manifest, result['reviews'])
        return result

    state()  # Fail explicitly on corrupt saved feedback before serving a page.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])

    @app.middleware('http')
    async def private_response(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).with_name('evaluation.html'), media_type='text/html')

    @app.get('/api/batch')
    def batch():
        # Original scores and rankings are intentionally omitted from the review UI response.
        return {'batch_id': manifest['batch_id'], 'source_name': manifest['source_name'],
                'cohort': manifest['cohort'],
                'denominator': manifest['denominator'], 'required_passes': manifest['required_passes'],
                'samples': [{k: v for k, v in s.items() if k not in ('original_rank', 'original_score', 'scorer')}
                            for s in manifest['samples']]}

    @app.get('/api/reviews')
    def reviews():
        with lock:
            return state()

    @app.get('/api/export')
    def export():
        with lock:
            value = state()
        return JSONResponse({'manifest': manifest, 'human_review': value},
                            headers={'Content-Disposition': 'attachment; filename="human-evaluation.json"'})

    @app.get('/media/{sample_id}')
    def media(sample_id: str):
        if sample_id not in by_id:
            raise HTTPException(404, '试听片段不存在')
        target = (root/by_id[sample_id]['file']).resolve()
        if not target.is_relative_to(root) or file_hash(target) != manifest['renders'][sample_id]['sha256']:
            raise HTTPException(409, '试听文件已变化，请勿继续评审')
        return FileResponse(target, media_type='video/mp4')

    @app.post('/api/review/{sample_id}')
    async def save(sample_id: str, request: Request):
        if request.headers.get('origin') not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            raise HTTPException(403, '请在本地试听页面保存')
        if not request.headers.get('content-type', '').startswith('application/json'):
            raise HTTPException(415, '仅接受 JSON 评审记录')
        raw = await request.body()
        if len(raw) > 16384:
            raise HTTPException(413, '评审记录过长')
        try:
            body = json.loads(raw)
            if (not isinstance(body, dict) or set(body) != {'batch_id', 'revision', 'answers', 'note'}
                    or body['batch_id'] != manifest['batch_id'] or sample_id not in by_id
                    or type(body['revision']) is not int):
                raise ValueError('评测批次或片段不匹配')
            validate_answers(body['answers'], body['note'])
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
        with lock:
            previous = state()
            if body['revision'] != previous['revision']:
                raise HTTPException(409, '记录已在其他页面更新，请刷新后重新核对')
            current = deepcopy(previous)
            current.pop('summary', None)
            current['revision'] += 1
            current['reviews'][sample_id] = {'answers': body['answers'], 'note': body['note'],
                                             'recorded_at': datetime.now(timezone.utc).isoformat(),
                                             'provenance': 'user_submitted_local_form'}
            target = history/f"review-{current['revision']:06}.json"
            temporary = target.with_suffix('.tmp')
            with temporary.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(current, ensure_ascii=False, indent=2)+'\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.rename(target)
            current['summary'] = summary(manifest, current['reviews'])
            return current

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--port', type=int, default=8307)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.folder, args.port), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
