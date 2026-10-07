from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from backend.tests.test_context_proposals import case
from clipforge.decision.candidates import content_hash
from clipforge.decision.llm import LLMFailure
from clipforge.decision.semantic_comparison import check_plan, execute_batch, execute_once, prepare_comparison, validate_response


class SemanticComparisonTests(unittest.TestCase):
    def setUp(self):
        self.a,self.c=case(['请问']+['这个问题有解释。']*19)
        self.plan=prepare_comparison(self.a,self.c,[[0,20],[0,40]])

    def response(self):
        return dict(assessments=[dict(id=o['id'],standalone='uncertain',opening='yes',ending='uncertain',
            single_topic='uncertain',reason='合成测试，不代表真实判断',option_quote=o['text'][:6],
            context_quote=self.plan['input']['context']['text'][:6]) for o in self.plan['input']['options']])

    def test_source_bound_plan_no_labels_or_credentials(self):
        before=content_hash([self.a,self.c])
        check_plan(self.plan)
        self.assertEqual(self.plan,prepare_comparison(self.a,self.c,[[0,20],[0,40]]))
        self.assertEqual(before,content_hash([self.a,self.c]))
        self.assertEqual(set(self.plan['input']),{'options','context'})
        self.assertEqual(self.plan['payload']['max_tokens'],512)

    def test_rehashed_wrong_endpoint_prompt_or_limit_is_rejected(self):
        for edit in (lambda p:p.update(endpoint='https://example.com'),
                     lambda p:p['payload'].update(max_tokens=900),
                     lambda p:p['payload']['messages'][0].update(content='changed')):
            p=deepcopy(self.plan); edit(p)
            p['plan_sha256']=content_hash({k:v for k,v in p.items() if k!='plan_sha256'})
            with self.assertRaises(ValueError): check_plan(p)

    def test_invalid_times_and_half_words_rejected(self):
        for ranges in ([[.5,20],[0,40]],[[0,10],[0,40]],[[0,20],[0,60]]):
            with self.assertRaises(ValueError): prepare_comparison(self.a,self.c,ranges)

    def test_quotes_unknown_ids_duplicate_assessments_and_score_injection_rejected(self):
        for edit in (lambda r:r['assessments'][0].update(option_quote='没有说过的话'),
                     lambda r:r['assessments'][0].update(id='C'),
                     lambda r:r['assessments'][1].update(id='A'),
                     lambda r:r['assessments'][0].update(score=99)):
            r=self.response(); edit(r)
            with self.assertRaises(ValueError): validate_response(json.dumps(r),self.plan)
        result=validate_response(json.dumps(self.response()),self.plan)
        self.assertFalse(result['automatic_acceptance'])

    def test_exact_authorization_required_and_attempt_cannot_repeat(self):
        calls=[]
        def transport(*args):
            calls.append(1)
            return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(self.response())))],
                        usage=dict(prompt_tokens=100,completion_tokens=100))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): execute_once(self.plan,'synthetic-key','wrong',tmp,transport=transport)
            self.assertEqual(calls,[])
            r=execute_once(self.plan,'synthetic-key',self.plan['plan_sha256'],tmp,transport=transport)
            self.assertEqual(r['status'],'format_and_citations_verified')
            with self.assertRaises(FileExistsError): execute_once(self.plan,'synthetic-key',self.plan['plan_sha256'],tmp,transport=transport)
            self.assertEqual(len(calls),1)
            self.assertNotIn('synthetic-key',''.join(p.read_text(encoding='utf-8') for p in Path(tmp).glob('*.json')))

    def test_timeout_has_one_attempt_and_no_secret_output(self):
        calls=[]
        def transport(*args):
            calls.append(1)
            raise LLMFailure('timeout',True)
        with tempfile.TemporaryDirectory() as tmp:
            r=execute_once(self.plan,'synthetic-key',self.plan['plan_sha256'],tmp,transport=transport)
            self.assertEqual(r['error'],'timeout')
            self.assertEqual(len(calls),1)

    def test_batch_stops_after_first_failure_and_validates_all_approvals_upfront(self):
        second=prepare_comparison(self.a,self.c,[[0,40],[0,20]])
        calls=[]
        def transport(*args):
            calls.append(1)
            raise LLMFailure('free_quota_exhausted')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                execute_batch([self.plan,second],'key',[self.plan['plan_sha256'],'wrong'],tmp,transport=transport)
            self.assertEqual(calls,[])
            results=execute_batch([self.plan,second],'key',[self.plan['plan_sha256'],second['plan_sha256']],tmp,transport=transport)
            self.assertEqual(len(results),1)
            self.assertEqual(len(calls),1)


if __name__=='__main__': unittest.main()
