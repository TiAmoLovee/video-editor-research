from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from clipforge.decision.boundary_optimizer import (BoundaryConfig, BoundedClient, compact_packet,
    decode_choice, optimize, settings_snapshot)
from clipforge.decision.candidates import ROOT, content_hash, generate_candidates
from clipforge.decision.llm import LLMFailure
from clipforge.decision.scoring import score_candidates
from clipforge.decision.sentence_repair import build_sentence_choices
from backend.tests.test_candidates import fixture


def snapshot(**changes):
    cfg = BoundaryConfig(endpoint='https://model.example/v1/chat/completions', **changes)
    prompts = {n: (ROOT/f'prompts/boundaries/{n}-v1.txt').read_text(encoding='utf-8') for n in ('punctuation', 'selection')}
    return dict(mode='sentence', config=asdict(cfg), prompts=prompts, prompts_sha256=content_hash(prompts))


def response(answer, usage=None):
    return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(answer)))],
                usage=usage or dict(prompt_tokens=100, completion_tokens=20, total_tokens=120))


class BoundaryOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name)/'budget.sqlite3'
        self.a = fixture([(i*10, i*10+10) for i in range(6)])
        for word, sentence in zip(self.a['words'], self.a['sentences']):
            word['text'] = sentence['text'] = '这是第'+word['id']+'个具体问题现在已经说完'
        self.rule = score_candidates(generate_candidates(self.a), self.a)
        self.punctuation = ''.join(w['text']+'。' for w in self.a['words'])

    def valid_response(self, cfg, payload, key, timeout):
        content = payload['messages'][1]['content']
        if content.startswith('{'):
            packet = json.loads(content)
            group = packet['allowed_ranges'].split(';')[0]
            if group:
                lo, ends = group.split(':')
                choice = lo+'-'+ends.split(',')[0]
            else:
                choice = 'keep'
            return response(dict(choice=choice, reason='使用原文中的完整范围'))
        return response(dict(punctuated=self.punctuation))

    def run_optimizer(self, transport, **config):
        return optimize(self.rule, self.a, snapshot(**config), api_key='fake-test-key', transport=transport, ledger=self.ledger)

    def test_success_preserves_original_scores_all_slots_and_no_human_labels(self):
        before = deepcopy((self.rule, self.a))
        transport = Mock(side_effect=self.valid_response)
        result = self.run_optimizer(transport)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(result['items']), result['denominator'])
        self.assertEqual((self.rule, self.a), before)
        self.assertEqual(transport.call_count, result['denominator']+1)
        self.assertEqual(result['usage']['retries'], 0)
        self.assertTrue(all(i['score'] is i['human_pass'] is None for i in result['items']))
        self.assertIsNone(result['fallback_reason'])

    def test_failure_stops_and_does_not_publish_partial_choices(self):
        transport = Mock(side_effect=[response({'punctuated': self.punctuation}), response({'choice':'999-999','reason':'不在选项中'})])
        result = self.run_optimizer(transport)
        self.assertEqual(result['status'], 'fallback')
        self.assertEqual(result['items'], [])
        self.assertEqual(transport.call_count, 2)

    def test_budget_seed_and_disabled_mode_prevent_transport(self):
        transport = Mock()
        self.assertIsNone(optimize(self.rule, self.a, {'mode':'off'}, transport=transport))
        result = self.run_optimizer(transport, max_total_tokens=2000, initial_used_tokens=1999)
        self.assertEqual(result['fallback_reason'], 'token_budget')
        transport.assert_not_called()

    def test_unknown_usage_reserves_budget_and_blocks_later_tasks(self):
        transport = Mock(return_value=response({'punctuated':self.punctuation}, usage={'total_tokens':1}))
        result = self.run_optimizer(transport)
        self.assertEqual(result['fallback_reason'], 'unknown_usage')
        self.assertEqual(result['usage']['unknown_usage_requests'], 1)
        second = self.run_optimizer(transport)
        self.assertEqual(second['fallback_reason'], 'unknown_usage')
        self.assertEqual(transport.call_count, 1)

    def test_network_failure_is_not_retried(self):
        transport = Mock(side_effect=LLMFailure('network_error', True))
        result = self.run_optimizer(transport)
        self.assertEqual(result['status'], 'fallback')
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result['usage']['unknown_usage_requests'], 1)

    def test_shared_ledger_settles_actual_usage_and_initial_seed_only_once(self):
        cfg = BoundaryConfig(endpoint='https://model.example/v1/chat/completions', max_total_tokens=20000, initial_used_tokens=15000)
        transport = Mock(return_value=response({'ok':True}))
        first = BoundedClient(cfg, api_key='fake', transport=transport, ledger=self.ledger)
        first.request('x','y',64)
        second = BoundedClient(cfg, api_key='fake', transport=transport, ledger=self.ledger)
        second.request('x','y',64)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(second.usage['charged_tokens'], 120)

    def test_compact_representation_keeps_text_all_options_and_exact_original_alias(self):
        c = dict(id='arbitrary', start=10, end=40)
        plan = build_sentence_choices(self.a, [c], self.punctuation)
        packet, mapping = compact_packet(plan, self.a, plan['candidates'][0])
        self.assertEqual(len(mapping), len(plan['candidates'][0]['options']))
        self.assertEqual(''.join(b[3] for b in packet['source_blocks']), ''.join(w['text'] for w in self.a['words']))
        if mapping:
            code = next(iter(mapping))
            self.assertEqual(decode_choice({'choice':code.replace('-',':'),'reason':'x'},packet,mapping)['choice'],mapping[code])
        original = [b[0] for b in packet['source_blocks'] if b[1] == c['start']]
        ending = [b[0] for b in packet['source_blocks'] if b[2] == c['end']]
        if original and ending:
            code = f'{original[0]}:{ending[0]}'
            self.assertEqual(decode_choice({'choice':code,'reason':'x'},packet,mapping)['choice'],'keep')
            packet['original']['text'] = 'changed'
            with self.assertRaises(ValueError):
                decode_choice({'choice':code,'reason':'x'},packet,mapping)
        with self.assertRaises(ValueError):
            decode_choice({'choice':'0-999','reason':'x'},packet,mapping)

    def test_invalid_configuration_and_scorer_combination_do_not_send(self):
        with patch.dict('os.environ', {'CLIPFORGE_BOUNDARY_REPAIR':'1', 'CLIPFORGE_SCORER':'llm'}):
            self.assertTrue(settings_snapshot()['configuration_error'])
        with self.assertRaises(ValueError):
            BoundaryConfig(max_total_tokens=1, initial_used_tokens=2)


if __name__ == '__main__':
    unittest.main()
