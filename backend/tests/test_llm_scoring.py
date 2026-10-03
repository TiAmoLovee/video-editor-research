"""离线故障注入与本机 HTTP 测试，不使用真实密钥或外部模型。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from clipforge.decision.candidates import ROOT, content_hash, generate_candidates
from clipforge.decision.llm import (LLMConfig, LLMFailure, LLMScorer, apply_llm, http_transport,
                                    parse_score, rate_slot, safe_settings_snapshot)
from clipforge.decision.presentation import candidate_page
from clipforge.decision.scoring import score_candidates, validate_scored
from clipforge.decision.scorers import default_registry
from backend.tests.test_candidates import fixture


def response(score=80, **changes):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'score':score,'reasons':['内容明确，但结尾需要上下文。']})}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20},**changes}


def snapshot(**changes):
    cfg = LLMConfig(endpoint='https://model.example/v1/chat/completions',model='test-model',**changes)
    prompt = (ROOT/'prompts/scoring/llm-v1.txt').read_text(encoding='utf-8')
    return {'mode':'llm','config':cfg.to_dict(),'prompt':prompt,'prompt_sha256':content_hash(prompt)}


class LLMScoringTests(unittest.TestCase):
    def setUp(self):
        self.analysis=fixture([(0,10),(10,20),(20,30)])
        self.rule=score_candidates(generate_candidates(self.analysis),self.analysis)
        self.transport=Mock(return_value=response())
        self.limiter=Mock()

    def apply(self, settings=None, **kwargs):
        return apply_llm(self.rule,self.analysis,settings or snapshot(),api_key='fake-local-test-key',
                         transport=self.transport,limiter=self.limiter,**kwargs)

    def test_registered_success_preserves_baseline_and_input_boundaries(self):
        self.assertIsInstance(default_registry().create('llm',config=LLMConfig(),prompt='JSON'),LLMScorer)
        before=deepcopy(self.rule)
        self.transport.side_effect=[response(40),response(90),response(70)]
        result=self.apply()
        self.assertEqual(result['scoring']['scorer'],'llm')
        self.assertEqual([c['score'] for c in result['candidates']],[90,70,40])
        self.assertEqual(self.rule,before)
        self.assertEqual(result['windows_sha256'],before['windows_sha256'])
        self.assertEqual(result['scoring']['llm']['usage']['requests'],3)
        self.assertIsNone(result['scoring']['llm']['usage']['estimated_known_cost'])
        self.assertEqual(candidate_page(result,10,0)['scorer'],'llm')
        for old in before['candidates']:
            new=next(c for c in result['candidates'] if c['id']==old['id'])
            for key in ['text','start','end','source_sentences','features']:
                self.assertEqual(new[key],old[key])
            self.assertEqual(new['rule_score'],old['score'])
        sent=self.transport.call_args.args[1]
        self.assertEqual(sent['response_format']['type'],'json_schema')
        self.assertNotIn('tools',sent)

    def test_invalid_output_variants_are_rejected(self):
        bad=['not JSON','```json\n{}\n```','[]','{"score":true,"reasons":["x"]}',
             '{"score":NaN,"reasons":["x"]}','{"score":2,"score":3,"reasons":["x"]}',
             '{"score":101,"reasons":["x"]}','{"score":2,"reasons":[]}',
             '{"score":2,"reasons":[" "]}','{"score":2,"reasons":["x"],"start":9}']
        bad.append('['*2000+']'*2000)
        for text in bad:
            with self.subTest(text=text),self.assertRaises(LLMFailure):parse_score(text)

    def test_partial_success_then_failure_reverts_entire_batch(self):
        self.transport.side_effect=[response(99),LLMFailure('invalid_key')]
        result=self.apply()
        self.assertEqual(result['scoring']['llm']['fallback_reason'],'invalid_key')
        self.assertEqual(result['scoring']['scorer'],'rule')
        self.assertEqual([(c['id'],c['score'],c['reasons']) for c in result['candidates']],
                         [(c['id'],c['score'],c['reasons']) for c in self.rule['candidates']])
        self.assertEqual(result['scoring']['llm']['usage']['unknown_usage_requests'],1)
        self.assertEqual(self.transport.call_count,2)

    def test_missing_key_configuration_disabled_and_limit_do_not_call(self):
        with patch.dict(os.environ,{'CLIPFORGE_SCORER':'rule'}):
            self.assertEqual(safe_settings_snapshot(),{'mode':'rule'})
        result=apply_llm(self.rule,self.analysis,snapshot(),api_key='',transport=self.transport)
        self.assertEqual(result['scoring']['llm']['fallback_reason'],'missing_key')
        result=self.apply(snapshot(max_candidates=1))
        self.assertEqual(result['scoring']['llm']['fallback_reason'],'candidate_limit')
        with patch.dict(os.environ,{'CLIPFORGE_SCORER':'llm','CLIPFORGE_LLM_CONFIG':'does-not-exist.json'}):
            invalid=safe_settings_snapshot()
        self.assertEqual(self.apply(invalid)['scoring']['llm']['fallback_reason'],'invalid_configuration')
        self.transport.assert_not_called()

    def test_retry_transient_errors_but_not_auth_and_stop_after_limit(self):
        for code in ['timeout','network_error','rate_limited','provider_error']:
            with self.subTest(code=code),patch('clipforge.decision.llm.time.sleep'):
                self.transport.reset_mock()
                self.transport.side_effect=LLMFailure(code,True)
                result=self.apply()
                self.assertEqual(self.transport.call_count,2)
                self.assertEqual(result['scoring']['llm']['usage']['retries'],1)
                self.assertEqual(result['scoring']['llm']['fallback_reason'],code)
        self.transport.reset_mock();self.transport.side_effect=LLMFailure('invalid_key')
        self.apply();self.assertEqual(self.transport.call_count,1)

    def test_successful_retry_and_usage_from_invalid_response_are_accounted(self):
        self.transport.side_effect=[LLMFailure('rate_limited',True),response(),response(),response()]
        with patch('clipforge.decision.llm.time.sleep'):
            result=self.apply(snapshot(input_price_per_million=1,output_price_per_million=2))
        usage=result['scoring']['llm']['usage']
        self.assertEqual((usage['requests'],usage['prompt_tokens'],usage['unknown_usage_requests']),(4,300,1))
        self.assertAlmostEqual(usage['estimated_known_cost'],.00042)
        self.assertFalse(usage['total_cost_known'])
        self.transport.side_effect=None
        self.transport.return_value=response(101)
        result=self.apply()
        self.assertEqual(result['scoring']['llm']['usage']['prompt_tokens'],100)
        self.assertEqual(result['scoring']['llm']['fallback_reason'],'invalid_output')

    def test_budgets_and_deadline_prevent_more_requests(self):
        for settings,reason in [(snapshot(max_task_reserved_tokens=1),'token_budget'),
                                (snapshot(input_price_per_million=1,output_price_per_million=2,max_task_cost=0),'cost_budget')]:
            self.assertEqual(self.apply(settings)['scoring']['llm']['fallback_reason'],reason)
        self.transport.assert_not_called()
        scorer=LLMScorer(LLMConfig(endpoint='https://test.example/v1/chat/completions',model='x'),
                         'JSON',api_key='fake',transport=self.transport,limiter=self.limiter)
        scorer.deadline=time.monotonic()-1
        with self.assertRaisesRegex(LLMFailure,'task_timeout'):scorer.score(self.rule['candidates'][0],self.analysis)
        self.transport.assert_not_called()

    def test_refusal_truncation_missing_usage_and_malformed_envelope(self):
        for changed in [{'choices':[]},{'choices':[{'finish_reason':'stop','message':None}]},
                        {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]},
                        {'choices':[{'finish_reason':'stop','message':{'refusal':'no','content':None}}]}]:
            self.transport.return_value=response(**changed)
            self.assertEqual(self.apply()['scoring']['scorer'],'rule')
        self.transport.return_value=response(usage=None)
        usage=self.apply()['scoring']['llm']['usage']
        self.assertEqual(usage['unknown_usage_requests'],3)
        self.assertFalse(usage['total_cost_known'])

    def test_config_rejects_unsafe_endpoints_and_nonfinite_limits(self):
        for endpoint in ['http://external.example/v1/chat/completions','https://user:key@a/v1/chat/completions',
                         'https://a/v1/chat/completions?key=secret','https://a:bad/v1/chat/completions']:
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):LLMConfig(endpoint=endpoint)
        for kw in [{'max_attempts':0},{'timeout_seconds':float('nan')},{'requests_per_minute':True},
                   {'input_price_per_million':-1},{'max_task_cost':1}]:
            with self.assertRaises(ValueError):LLMConfig(**kw)

    def test_tampering_model_output_baseline_and_modes_rejected(self):
        good=self.apply()
        for mutate in [lambda d:d['candidates'][0].update(start=1),
                       lambda d:d['candidates'][0].update(rule_score=99),
                       lambda d:d['candidates'][0].update(scorer='rule'),
                       lambda d:d['scoring']['llm'].update(fallback_reason='invalid_key')]:
            bad=deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):validate_scored(bad,self.analysis)

    def test_empty_candidates_no_network(self):
        empty=fixture([]);rule=score_candidates(generate_candidates(empty),empty)
        result=apply_llm(rule,empty,snapshot(),api_key='fake',transport=self.transport)
        self.assertEqual(result['candidate_count'],0)
        self.assertEqual(result['scoring']['scorer'],'rule')
        self.transport.assert_not_called()

    def test_shared_rate_limiter_spacing_and_deadline(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'CLIPFORGE_DATA_DIR':directory}):
            cfg=LLMConfig(requests_per_minute=600)
            start=time.monotonic()
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda _:rate_slot(cfg,start+2),range(3)))
            self.assertGreaterEqual(time.monotonic()-start,.18)
            with self.assertRaisesRegex(LLMFailure,'task_timeout'):
                rate_slot(cfg,time.monotonic()+.01)


class HTTPTransportTests(unittest.TestCase):
    def test_real_timeout_and_closed_port(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                time.sleep(.2)
                self.close_connection=True
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        cfg=LLMConfig(endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',model='fake')
        try:
            with self.assertRaisesRegex(LLMFailure,'timeout'):http_transport(cfg,{},'fake',.03)
        finally:
            server.shutdown();server.server_close();thread.join()
        # Windows 的本机关闭端口也可能先超时；两者都必须是可重试的连通性失败。
        with self.assertRaises(LLMFailure) as raised:http_transport(cfg,{},'fake',.2)
        self.assertIn(raised.exception.code,('network_error','timeout'))
        self.assertTrue(raised.exception.retryable)

    def test_real_loopback_success_auth_retry_codes_and_malformed_responses(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                status=getattr(self.server,'test_status',200)
                self.send_response(status);self.end_headers()
                self.wfile.write(getattr(self.server,'test_body',json.dumps(response()).encode()))
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            cfg=LLMConfig(endpoint=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',model='fake')
            self.assertEqual(http_transport(cfg,{'model':'fake'},'fake',1)['usage']['prompt_tokens'],100)
            for status,code,retry in [(401,'invalid_key',False),(403,'invalid_key',False),(429,'rate_limited',True),
                                       (503,'provider_error',True),(302,'provider_error',False)]:
                server.test_status=status
                with self.assertRaises(LLMFailure) as raised:http_transport(cfg,{},'fake',1)
                self.assertEqual((raised.exception.code,raised.exception.retryable),(code,retry))
            server.test_status=200;server.test_body=b'{not-json'
            with self.assertRaisesRegex(LLMFailure,'invalid_response'):http_transport(cfg,{},'fake',1)
            server.test_body=b'X'*262145
            with self.assertRaisesRegex(LLMFailure,'response_too_large'):http_transport(cfg,{},'fake',1)
            self.assertEqual(seen[0],{'model':'fake'})
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
