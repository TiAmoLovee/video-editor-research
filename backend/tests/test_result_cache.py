from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from clipforge.analysis.combine import combine_analysis
from clipforge.media.split import build_plan
from clipforge.services import result_cache as cache
from clipforge.services.pipeline import process_video
from clipforge.storage.jobs import create_job, database, job_dir


class ResultCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'CLIPFORGE_DATA_DIR': self.temp.name, 'CLIPFORGE_RESULT_CACHE': '1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.example = json.loads((Path(__file__).resolve().parents[2]/'docs/samples/analysis.example.json').read_text(encoding='utf-8'))

    def job(self, name='sample.mp4', data=b'test-source', options=None):
        task = str(uuid4()); folder = job_dir(task); folder.mkdir(parents=True)
        (folder/'source.mp4').write_bytes(data)
        if options is not None:
            (folder/'shot_options.json').write_text(json.dumps(options), encoding='utf-8')
        create_job(task, name, 'source.mp4')
        return task

    def descriptor(self, source, options_path, ffmpeg, ffprobe):
        options = json.loads(options_path.read_text()) if options_path.exists() else None
        d = {'source_sha256': cache.sha256(source), 'shot_options': options, 'test_configuration': 'fixed',
             'scoring_config': cache.load_config().to_dict()}
        return hashlib.sha256(cache.canonical(d).encode()).hexdigest(), d

    def pipeline(self):
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch('clipforge.services.result_cache.request_fingerprint', side_effect=self.descriptor))
        example = self.example
        media = {'normalized_sha256': example['media']['normalized_sha256'], 'time_reference': 'normalized_video',
                 'fps': 30, 'total_frames': int(example['media']['duration_seconds']*30),
                 'duration_seconds': example['media']['duration_seconds'], 'has_audio': example['media']['has_audio']}
        components = {}
        for short, kind, fields in [('shots','shot_analysis',['shots']), ('vad','vad_analysis',['speech','silence']),
                                    ('asr','asr_analysis',['words','sentences'])]:
            components[short] = {'schema_version':'0.1.0','artifact_kind':kind,'result_kind':'measured',
                                 'media':dict(media),'analyzer':example['analyzers'][short],
                                 **{name:deepcopy(example[name]) for name in fields}}
        stack.enter_context(patch('clipforge.services.pipeline.probe_video', return_value={}))
        stack.enter_context(patch('clipforge.services.pipeline.normalize_metadata', return_value={'source_file':'source.mp4'}))
        def normalize(source, destination, *args):
            Path(destination).write_bytes(b'normalized-test'); return {'video':{'width':1920,'height':1080}}
        self.normalize = stack.enter_context(patch('clipforge.services.pipeline.normalize_video', side_effect=normalize))
        stack.enter_context(patch('clipforge.services.pipeline.detect_shots', return_value=components['shots']))
        stack.enter_context(patch('clipforge.services.pipeline.detect_speech', return_value=components['vad']))
        self.asr = stack.enter_context(patch('clipforge.services.pipeline.transcribe_video', return_value=components['asr']))
        def split(source, destination, *args):
            folder = Path(destination); folder.mkdir()
            plan = {'total_frames':media['total_frames'],'clips':build_plan(media['total_frames'])}
            for clip in plan['clips']:
                (folder/clip['file']).write_bytes(b'test-clip')
            (folder/'clip_plan.json').write_text(json.dumps(plan),encoding='utf-8')
            return plan
        self.split = stack.enter_context(patch('clipforge.services.pipeline.split_video', side_effect=split))
        return components

    def test_repeated_renamed_input_skips_all_expensive_steps_and_zip_matches(self):
        self.pipeline(); a=self.job(); b=self.job('renamed.mp4')
        self.assertFalse(process_video(a)['cache_hit'])
        self.assertTrue(process_video(b)['cache_hit'])
        self.assertEqual((self.asr.call_count,self.normalize.call_count,self.split.call_count),(1,1,1))
        for name in ['shots.json','vad.json','asr.json','analysis.json','candidate_windows.json','candidates.json','clips/clip_001.mp4']:
            self.assertEqual((job_dir(a)/name).read_bytes(),(job_dir(b)/name).read_bytes())
        self.assertEqual(json.loads((job_dir(b)/'media_meta.json').read_text())['source_file'],'renamed.mp4')
        info=json.loads((job_dir(b)/'cache.json').read_text());self.assertFalse(info['transcription_executed'])
        self.assertIn('scoring_candidates', info['reused_stages'])
        with zipfile.ZipFile(job_dir(b)/'result.zip') as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(z.read('cache.json'),(job_dir(b)/'cache.json').read_bytes())
            self.assertEqual(z.read('candidates.json'),(job_dir(b)/'candidates.json').read_bytes())
        # 独立复制，修改新任务切片不会污染缓存源任务。
        (job_dir(b)/'clips/clip_001.mp4').write_bytes(b'changed')
        self.assertEqual((job_dir(a)/'clips/clip_001.mp4').read_bytes(),b'test-clip')

    def test_different_content_or_region_misses(self):
        self.pipeline()
        for task in [self.job(),self.job(data=b'other'),self.job(options={'method':'robust','region':[0,0,.5,1]})]:
            self.assertFalse(process_video(task)['cache_hit'])
        self.assertEqual(self.asr.call_count,3)

    def test_corrupt_or_missing_cache_recomputes_and_replaces_index(self):
        self.pipeline();a=self.job();process_video(a)
        (job_dir(a)/'clips/clip_001.mp4').write_bytes(b'bad-bytes')
        b=self.job()
        with self.assertLogs('clipforge.services.pipeline',level='WARNING'):
            self.assertFalse(process_video(b)['cache_hit'])
        c=self.job();self.assertTrue(process_video(c)['cache_hit'])
        (job_dir(b)/'asr.json').unlink()
        d=self.job()
        with self.assertLogs('clipforge.services.pipeline',level='WARNING'):
            self.assertFalse(process_video(d)['cache_hit'])
        self.assertEqual(self.asr.call_count,3)

    def test_failed_source_job_is_not_reused(self):
        self.pipeline();a=self.job();process_video(a)
        # 模拟索引指向状态异常的旧记录；公共接口禁止改写已结束任务。
        with database() as connection:
            connection.execute("UPDATE jobs SET status='FAILED' WHERE task_id=?", (a,))
        with self.assertLogs('clipforge.services.pipeline',level='WARNING'):
            self.assertFalse(process_video(self.job())['cache_hit'])

    def test_transcription_failure_does_not_publish_cache(self):
        self.pipeline(); self.asr.side_effect=RuntimeError('inference failed')
        with self.assertLogs('clipforge.services.pipeline',level='ERROR'), self.assertRaises(RuntimeError):
            process_video(self.job())
        self.assertEqual(list((Path(self.temp.name)/'result-cache-v2').glob('*.json')),[])

    def test_parallel_identical_jobs_run_transcription_once(self):
        self.pipeline();tasks=[self.job(),self.job()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(process_video,tasks))
        self.assertEqual(sorted(r['cache_hit'] for r in results),[False,True])
        self.assertEqual(self.asr.call_count,1)

    def test_disabled_cache_runs_each_job_and_model_error_falls_back(self):
        self.pipeline()
        with patch.dict(os.environ,{'CLIPFORGE_RESULT_CACHE':'0'}):
            process_video(self.job());process_video(self.job())
        self.assertEqual(self.asr.call_count,2)
        with patch.object(cache,'request_fingerprint',side_effect=ValueError('bad model')), self.assertLogs('clipforge.services.pipeline',level='WARNING'):
            result=process_video(self.job())
        self.assertFalse(result['cache_hit'])

    def test_fingerprint_changes_for_language_model_code_and_content(self):
        source=Path(self.temp.name)/'video.mp4';source.write_bytes(b'a')
        with patch.object(cache,'verify_model',return_value={'revision':'one'}) as verify, \
             patch.object(cache,'version',return_value='1'), \
             patch.object(cache.subprocess,'run',return_value=type('Tool',(),{'stdout':'ffmpeg-test'})()):
            key,_=cache.fingerprint(source,None,'ffmpeg','ffprobe')
            with patch.object(cache, 'load_config', return_value=replace(cache.load_config(), keyword_weight=20)):
                self.assertNotEqual(key, cache.fingerprint(source,None,'ffmpeg','ffprobe')[0])
            with patch.dict(os.environ,{'CLIPFORGE_ASR_LANGUAGE':'zh'}):
                self.assertNotEqual(key,cache.fingerprint(source,None,'ffmpeg','ffprobe')[0])
            verify.return_value={'revision':'two'}
            self.assertNotEqual(key,cache.fingerprint(source,None,'ffmpeg','ffprobe')[0])
            verify.return_value={'revision':'one'}
            self.assertNotEqual(key,cache.fingerprint(source,{'method':'robust'},'ffmpeg','ffprobe')[0])
            with patch.object(cache,'version',return_value='2'):
                self.assertNotEqual(key,cache.fingerprint(source,None,'ffmpeg','ffprobe')[0])
            source.write_bytes(b'b')
            self.assertNotEqual(key,cache.fingerprint(source,None,'ffmpeg','ffprobe')[0])

    def test_invalid_index_and_path_traversal_are_not_read(self):
        root=Path(self.temp.name)
        for name in ['../secret','/secret','C:/secret','clips/../../secret','clips\\secret']:
            with self.assertRaises(ValueError):cache.safe_path(root,name)
        self.pipeline();a=self.job();process_video(a)
        index=next((root/'result-cache-v2').glob('*.json'));index.write_text('{bad')
        with self.assertLogs('clipforge.services.pipeline',level='WARNING'):
            self.assertFalse(process_video(self.job())['cache_hit'])

    def test_missing_candidates_recomputes_and_configuration_change_misses(self):
        self.pipeline(); first=self.job();process_video(first)
        (job_dir(first)/'candidates.json').unlink()
        with self.assertLogs('clipforge.services.pipeline',level='WARNING'):
            self.assertFalse(process_video(self.job())['cache_hit'])
        with patch.object(cache, 'load_config', return_value=replace(cache.load_config(),keyword_weight=20)):
            changed=self.job();self.assertFalse(process_video(changed)['cache_hit'])
            result=json.loads((job_dir(changed)/'candidates.json').read_text(encoding='utf-8'))
            self.assertEqual(result['scoring']['config']['keyword_weight'],20)
            self.assertTrue(process_video(self.job())['cache_hit'])
        self.assertEqual(self.asr.call_count,3)

    def test_process_lock_is_released_after_termination(self):
        ready=Path(self.temp.name)/'ready'
        code="from clipforge.services.result_cache import key_lock; from pathlib import Path; import time\nwith key_lock('test-lock'):\n Path("+repr(str(ready))+").write_text('ready')\n time.sleep(30)"
        child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while not ready.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists())
            with self.assertRaises(TimeoutError):
                with cache.key_lock('test-lock',timeout=.1):pass
        finally:
            child.terminate();child.communicate(timeout=5)
        with cache.key_lock('test-lock',timeout=1):pass


if __name__=='__main__':unittest.main()
