from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from backend.tests.test_candidates import fixture
from clipforge.decision.candidates import generate_candidates, content_hash, WindowOptions
from clipforge.decision.scoring import score_candidates
from clipforge.decision.boundary_review import prepare_review, validate_review, check_plan
from clipforge.decision.boundary_review_call import execute_once, execute_batch
from clipforge.decision.llm import LLMFailure


class BoundaryReviewTests(unittest.TestCase):
    def setUp(self):
        self.analysis = fixture([(0, 10), (10.5, 20), (20.6, 25), (25.5, 32)])
        for s, w, text in zip(self.analysis['sentences'], self.analysis['words'],
                              ['这里是提问', '这是第一部分回答', '但是还需要解释', '这是最后的说明']):
            s['text'] = w['text'] = text
        self.scored = score_candidates(generate_candidates(self.analysis), self.analysis)
        self.candidate = next(c for c in self.scored['candidates'] if c['start']==0 and c['end']==20)
        self.plan = prepare_review(self.analysis, self.scored, self.candidate['id'])

    def assessment(self):
        return {'ending_status':'uncertain','reason':'后文可能仍在解释，需试听',
                'evidence':[{'sentence_id':'s1','quote':'第一部分回答'},
                            {'sentence_id':'s2','quote':'但是还需要解释'}],
                'recommended_candidate_id':self.plan['input']['allowed_alternatives'][0]['id']}

    def test_prepare_is_deterministic_and_does_not_send_scores_or_human_labels(self):
        before = content_hash([self.analysis,self.scored])
        self.assertEqual(self.plan, prepare_review(self.analysis,self.scored,self.candidate['id']))
        text=self.plan['payload']['messages'][1]['content']
        self.assertNotIn('score', text)
        self.assertNotIn('rank', text)
        self.assertEqual(before, content_hash([self.analysis,self.scored]))
        check_plan(self.plan)

    def test_existing_extension_must_not_jump_long_gap(self):
        a=fixture([(0,20),(30,40)])
        scored=score_candidates(generate_candidates(a,WindowOptions(max_gap_seconds=20)),a)
        c=next(c for c in scored['candidates'] if c['end']==20)
        self.assertFalse(prepare_review(a,scored,c['id'])['input']['allowed_alternatives'])

    def test_exact_citations_and_known_recommendation_still_need_review(self):
        result=validate_review(json.dumps(self.assessment()),self.plan)
        self.assertEqual(result['semantic_quality'],'requires_review')
        self.assertFalse(result['automatic_acceptance'])

    def test_fabricated_quote_unknown_range_outside_only_and_inconsistent_status_rejected(self):
        mutations=[lambda a:a['evidence'][0].update(quote='模型补写的内容'),
                   lambda a:a.update(recommended_candidate_id='invented'),
                   lambda a:a.update(evidence=[a['evidence'][1]]),
                   lambda a:a.update(evidence=[a['evidence'][0]]),
                   lambda a:a.update(ending_status='complete'),
                   lambda a:a.update(score=100)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value=self.assessment(); mutate(value)
                with self.assertRaises(ValueError):
                    validate_review(json.dumps(value),self.plan)
        with self.assertRaises(ValueError):
            validate_review('{"reason":"a","reason":"b"}',self.plan)

    def test_changed_plan_or_approval_prevents_transport(self):
        def never(*args):
            self.fail('must not send')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                execute_once(self.plan,'test-secret','wrong-hash',tmp,transport=never)
            changed=deepcopy(self.plan); changed['payload']['max_tokens']=1024
            with self.assertRaises(ValueError):
                execute_once(changed,'test-secret',changed['plan_sha256'],tmp,transport=never)
            self.assertEqual(list(Path(tmp).glob('*')),[])

    def test_success_is_one_request_and_receipt_blocks_repeated_execution(self):
        calls=[]
        def fake(*args):
            calls.append(args)
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.assessment())}}],
                    'usage':{'prompt_tokens':200,'completion_tokens':100}}
        with tempfile.TemporaryDirectory() as tmp:
            result=execute_once(self.plan,'test-secret',self.plan['plan_sha256'],tmp,transport=fake)
            self.assertEqual(result['status'],'format_and_citations_verified')
            with self.assertRaises(FileExistsError):
                execute_once(self.plan,'test-secret',self.plan['plan_sha256'],tmp,transport=fake)
            self.assertEqual(len(calls),1)
            self.assertNotIn('test-secret',''.join(p.read_text(encoding='utf-8') for p in Path(tmp).glob('*')))

    def test_timeout_and_truncated_response_do_not_retry_or_accept(self):
        for mode in ('timeout','length'):
            calls=[]
            def fake(*args):
                calls.append(1)
                if mode=='timeout':
                    raise LLMFailure('timeout',True)
                return {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}
            with tempfile.TemporaryDirectory() as tmp:
                result=execute_once(self.plan,'test-secret',self.plan['plan_sha256'],tmp,transport=fake)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(len(calls),1)
                self.assertFalse(result['automatic_acceptance'])

    def test_batch_stops_after_first_failure_and_validates_all_approvals_upfront(self):
        other = next(c for c in self.scored['candidates'] if c['start']==0 and c['end']==25)
        second = prepare_review(self.analysis,self.scored,other['id'])
        plans = [self.plan,second]
        hashes = [p['plan_sha256'] for p in plans]
        calls=[]
        def fake(*args):
            calls.append(1)
            raise LLMFailure('free_quota_exhausted')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                execute_batch(plans,'test-secret',[hashes[0],'bad'],tmp,transport=fake)
            self.assertEqual(calls,[])
            results=execute_batch(plans,'test-secret',hashes,tmp,transport=fake)
            self.assertEqual(len(results),1)
            self.assertEqual(calls,[1])
            self.assertEqual(results[0]['error'],'free_quota_exhausted')

    def test_invalid_model_text_is_retained_with_fixed_diagnostic_and_key_redacted(self):
        invalid=self.assessment()
        invalid['reason']='test-secret'
        invalid['evidence'][0]['quote']='原文中没有这句话'
        content=json.dumps(invalid,ensure_ascii=False)
        def fake(*args):
            return {'choices':[{'finish_reason':'stop','message':{'content':content}}]}
        with tempfile.TemporaryDirectory() as tmp:
            result=execute_once(self.plan,'test-secret',self.plan['plan_sha256'],tmp,transport=fake)
            self.assertEqual(result['validation_error'],'invalid_source_citation')
            self.assertEqual(result['status'],'failed')
            self.assertIn('原文中没有这句话',result['unvalidated_model_text'])
            self.assertNotIn('test-secret',''.join(p.read_text(encoding='utf-8') for p in Path(tmp).glob('*')))
            self.assertFalse(result['automatic_acceptance'])

    def test_invalid_json_gets_specific_code_without_provider_error_body(self):
        def fake(*args):
            return {'choices':[{'finish_reason':'stop','message':{'content':'not JSON'}}]}
        with tempfile.TemporaryDirectory() as tmp:
            result=execute_once(self.plan,'test-secret',self.plan['plan_sha256'],tmp,transport=fake)
            self.assertEqual(result['validation_error'],'invalid_json')
            self.assertEqual(result['unvalidated_model_text'],'not JSON')
