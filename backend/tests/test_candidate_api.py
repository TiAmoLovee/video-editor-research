from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from clipforge.api import app
from clipforge.decision.candidates import generate_candidates
from clipforge.decision.scoring import score_candidates
from clipforge.storage.jobs import create_job, job_dir, claim_job, update_job
from backend.tests.test_candidates import fixture


class CandidateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{'CLIPFORGE_DATA_DIR':self.temp.name})
        self.env.start();self.addCleanup(self.env.stop)
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.analysis=fixture([(0,10),(10,20),(20,30)])
        self.result=score_candidates(generate_candidates(self.analysis),self.analysis)

    def job(self, data=None, *, completed=True, legacy=False, mapped='candidates.json'):
        task=str(uuid4());folder=job_dir(task);folder.mkdir(parents=True)
        create_job(task,'example.mp4','source.mp4')
        if completed:
            claim_job(task)
            (folder/'candidates.json').write_text(json.dumps(data or self.result),encoding='utf-8')
            update_job(task,'SUCCEEDED','done',100,result={'clip_count':0,'total_frames':930,
                       'clips':[],'files':{} if legacy else {'candidates.json':mapped}})
        return task

    def test_paginated_projection_matches_download_without_internal_fields(self):
        task=self.job()
        response=self.client.get(f'/tasks/{task}/candidates?limit=1&offset=1')
        self.assertEqual(response.status_code,200)
        page=response.json()
        self.assertEqual((page['total'],page['limit'],page['offset'],page['has_more']),(3,1,1,True))
        self.assertEqual(page['items'][0]['id'],self.result['candidates'][1]['id'])
        self.assertNotIn('features',page['items'][0])
        self.assertNotIn('config',page)
        downloaded=self.client.get(f'/tasks/{task}/files/candidates.json').json()
        self.assertEqual(downloaded,self.result)
        self.assertEqual(self.client.get(f'/tasks/{task}/candidates?offset=3').json()['items'],[])

    def test_empty_is_success_not_an_error(self):
        data=fixture([]); result=score_candidates(generate_candidates(data),data)
        page=self.client.get(f'/tasks/{self.job(result)}/candidates').json()
        self.assertEqual((page['total'],page['items'],page['has_more']),(0,[],False))

    def test_legacy_unfinished_unknown_and_invalid_pagination(self):
        self.assertEqual(self.client.get(f'/tasks/{self.job(legacy=True)}/candidates').status_code,404)
        self.assertEqual(self.client.get(f'/tasks/{self.job(completed=False)}/candidates').status_code,409)
        self.assertEqual(self.client.get(f'/tasks/{uuid4()}/candidates').status_code,404)
        task=self.job()
        for query in ('limit=0','limit=101','offset=-1'):
            self.assertEqual(self.client.get(f'/tasks/{task}/candidates?{query}').status_code,422)

    def test_corrupt_or_missing_result_is_explicit_without_path_leak(self):
        task=self.job();path=job_dir(task)/'candidates.json'
        path.write_text('{bad',encoding='utf-8')
        response=self.client.get(f'/tasks/{task}/candidates')
        self.assertEqual(response.status_code,500)
        self.assertNotIn(self.temp.name,response.text)
        path.unlink()
        self.assertEqual(self.client.get(f'/tasks/{task}/candidates').status_code,404)

    def test_invalid_score_and_path_escape_rejected(self):
        for value in (101,float('nan')):
            data=deepcopy(self.result);data['candidates'][0]['score']=value
            self.assertEqual(self.client.get(f'/tasks/{self.job(data)}/candidates').status_code,500)
        outside=Path(self.temp.name)/'outside.json';outside.write_text('{}')
        self.assertEqual(self.client.get(f'/tasks/{self.job(mapped="../../outside.json")}/candidates').status_code,404)

    def test_model_and_fallback_projection(self):
        from clipforge.decision.llm import apply_llm
        from backend.tests.test_llm_scoring import snapshot,response
        for key,expected in [('fake','llm'),('','rule')]:
            result=apply_llm(self.result,self.analysis,snapshot(),api_key=key,
                             transport=lambda *args:response(),limiter=lambda *args:None)
            page=self.client.get(f'/tasks/{self.job(result)}/candidates').json()
            self.assertEqual((page['scorer'],page['requested_scorer']),(expected,'llm'))
            self.assertIn('rule_score',page['items'][0])
            self.assertEqual(page['fallback_reason'],None if key else 'missing_key')


if __name__=='__main__':unittest.main()
