"""真实视频缓存冷/热对比，worker 每次在新进程执行；不代替 Docker/Celery 验收。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
from unittest.mock import patch
from uuid import uuid4
import zipfile


def worker(task, output):
    from clipforge.services import pipeline
    calls=[]
    original=pipeline.transcribe_video
    def transcribe(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    started=time.perf_counter()
    with patch.object(pipeline,'transcribe_video',side_effect=transcribe):
        result=pipeline.process_video(task)
    output.write_text(json.dumps({'worker_seconds':time.perf_counter()-started,
                                 'transcription_calls':len(calls),'result':result},indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worker-task')
    args=parser.parse_args()
    if args.worker_task:
        worker(args.worker_task,args.output);return
    if not args.video or not args.video.is_file() or args.output.exists():
        parser.error('视频须存在，输出目录须为新目录')
    args.output.mkdir(parents=True)
    os.environ['CLIPFORGE_DATA_DIR']=str((args.output/'data').resolve())
    os.environ['CLIPFORGE_RESULT_CACHE']='1'
    from fastapi.testclient import TestClient
    from clipforge.api import app
    from clipforge.storage.jobs import create_job,job_dir
    records=[]
    for run in range(3):
        task=str(uuid4());folder=job_dir(task);folder.mkdir(parents=True)
        source='source'+args.video.suffix.lower()
        shutil.copyfile(args.video,folder/source)
        create_job(task,args.video.name if run<2 else 'renamed'+args.video.suffix.lower(),source)
        timing=args.output/f'run-{run+1}.json'
        print(f'Running {run+1}/3 in fresh worker process',flush=True)
        subprocess.run([sys.executable,__file__,'--worker-task',task,'--output',str(timing)],check=True)
        record=json.loads(timing.read_text(encoding='utf-8'))
        with TestClient(app) as client:
            response=client.get('/tasks/'+task);response.raise_for_status();status=response.json()
            assert status['status']=='SUCCEEDED'
            downloads=status['result']['downloads']
            for name in ['analysis.json','asr.json','cache.json','candidate_windows.json','candidates.json']:
                response=client.get(downloads[name]);response.raise_for_status()
                assert response.content==(folder/name).read_bytes()
            record['cache']=json.loads((folder/'cache.json').read_text(encoding='utf-8'))
            analysis=json.loads((folder/'analysis.json').read_text(encoding='utf-8'))
            assert analysis['words'], '该验收素材需有真实转写词条'
            record['words']=len(analysis['words'])
            record['analysis_sha256']=hashlib.sha256((folder/'analysis.json').read_bytes()).hexdigest()
            candidates=json.loads((folder/'candidates.json').read_text(encoding='utf-8'))
            from clipforge.decision.scoring import validate_scored
            validate_scored(candidates,analysis)
            record['candidates_sha256']=hashlib.sha256((folder/'candidates.json').read_bytes()).hexdigest()
            record['candidate_count']=candidates['candidate_count']
            record['audio_status']=candidates['scoring']['audio']['status']
            response=client.get('/tasks/'+task+'/candidates?limit=2');response.raise_for_status()
            page=response.json()
            assert page['total']==candidates['candidate_count']
            assert [c['id'] for c in page['items']]==[c['id'] for c in candidates['candidates'][:2]]
            record['clip_sha256']={clip['file']:hashlib.sha256((folder/'clips'/clip['file']).read_bytes()).hexdigest()
                                  for clip in status['result']['clips']}
            with zipfile.ZipFile(folder/'result.zip') as archive:
                assert archive.testzip() is None
                for name in ['analysis.json','asr.json','cache.json','candidate_windows.json','candidates.json']:
                    assert archive.read(name)==(folder/name).read_bytes()
        assert record['result']['cache_hit']==(run>0)
        assert record['transcription_calls']==(1 if run==0 else 0)
        if records:
            assert record['analysis_sha256']==records[0]['analysis_sha256']
            assert record['candidates_sha256']==records[0]['candidates_sha256']
            assert record['clip_sha256']==records[0]['clip_sha256']
        records.append(record)
        print(json.dumps({'run':run+1,'seconds':record['worker_seconds'],'hit':record['result']['cache_hit'],
                          'asr_calls':record['transcription_calls']},ensure_ascii=False),flush=True)
    reduction=1-statistics.median(r['worker_seconds'] for r in records[1:])/records[0]['worker_seconds']
    report={'scope':'Native real small/base inference; fresh process each run; worker processing including cache key, validation, copying, ZIP and index publication. Excludes upload, queue wait and browser polling. HTTP adapters checked with TestClient, no real Celery/Docker acceptance.',
            'source':str(args.video.resolve()),'runs':records,'median_warm_worker_reduction':reduction,
            'passes_80_percent_target':reduction>=.8,'all_analysis_and_clip_hashes_equal':True,
            'all_download_and_zip_checks_passed':True}
    (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'worker_time_reduction':reduction,'passes_80_percent_target':reduction>=.8}),flush=True)


if __name__=='__main__':main()
