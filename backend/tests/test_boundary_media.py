from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from clipforge.api import app
from clipforge.decision.boundary_media import validate_report
from clipforge.decision.boundary_optimizer import optimize
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.storage.jobs import create_job, job_dir, claim_job, update_job
from backend.tests.test_candidates import fixture
from backend.tests.test_boundary_optimizer import snapshot, response


class BoundaryMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'CLIPFORGE_DATA_DIR':self.temp.name})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(app); self.addCleanup(self.client.close)
        self.analysis = fixture([(0,10),(10,20),(20,30)])
        self.scored = score_candidates(generate_candidates(self.analysis),self.analysis)
        transcript = ''.join(w['text'] for w in self.analysis['words'])
        answers = iter([{'punctuated':transcript}]+[{'choice':'keep','reason':'保留原范围'}]*10)
        self.report = optimize(self.scored,self.analysis,snapshot(),api_key='fake',
                               transport=lambda *args:response(next(answers)),ledger=Path(self.temp.name)/'budget.db')
        self.assertEqual(self.report['status'],'ready')

    def job(self):
        task=str(uuid4());folder=job_dir(task);folder.mkdir(parents=True)
        create_job(task,'sample.mp4','source.mp4');claim_job(task)
        files={name:name for name in ('analysis.json','candidates.json','boundary_optimization.json')}
        self.report['renders']={}
        for item in self.report['items']:
            if item['status']!='unverified':continue
            name='repair_'+item['id']+'.mp4';media=folder/'repaired'/name;media.parent.mkdir(exist_ok=True);media.write_bytes(b'media')
            self.report['renders'][item['id']]={'file':name,'sha256':hashlib.sha256(b'media').hexdigest(),
                'frames':item['end_frame_exclusive']-item['start_frame'],'duration_seconds':item['duration_seconds']}
            files[name]='repaired/'+name
        for name,obj in [('analysis.json',self.analysis),('candidates.json',self.scored),('boundary_optimization.json',self.report)]:
            (folder/name).write_text(json.dumps(obj),encoding='utf-8')
        update_job(task,'SUCCEEDED','done',100,result={'clip_count':0,'total_frames':930,'clips':[],'files':files})
        return task,folder

    def test_page_and_range_download_expose_unscored_video_without_human_approval(self):
        task,folder=self.job()
        validate_report(self.report,self.analysis,self.scored,folder)
        page=self.client.get(f'/tasks/{task}/candidates').json()
        optimized=page['boundary_optimization'];self.assertFalse(optimized['automatic_acceptance'])
        self.assertEqual(optimized['denominator'],len(self.report['items']))
        self.assertEqual(page['accepted_versions'],[])
        item=optimized['versions'][0];self.assertIsNone(item['score']);self.assertIsNone(item['human_pass'])
        response=self.client.get(item['video_url'],headers={'Range':'bytes=0-1'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,b'me')

    def test_changed_video_or_forged_label_cannot_be_played(self):
        task,folder=self.job();item=self.report['items'][0];name=self.report['renders'][item['id']]['file']
        (folder/'repaired'/name).write_bytes(b'changed')
        self.assertEqual(self.client.get(f'/tasks/{task}/files/{name}').status_code,409)
        for key,value in [('score',80),('human_pass',True),('text','changed')]:
            bad=deepcopy(self.report);bad['items'][0][key]=value
            with self.assertRaises(ValueError):validate_report(bad,self.analysis,self.scored)

    def test_no_partial_publication_and_source_identity_is_required(self):
        _,folder=self.job()
        for change in ({'status':'fallback'},{'analysis_sha256':'0'*64},{'renders':{}},{'denominator':999}):
            with self.assertRaises(ValueError):validate_report(dict(self.report,**change),self.analysis,self.scored,folder)


if __name__=='__main__':unittest.main()
