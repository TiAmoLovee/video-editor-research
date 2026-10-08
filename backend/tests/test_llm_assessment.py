"""V2 evidence, arithmetic, legacy compatibility and whole-task fallback; no network."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from clipforge.decision.assessment import DIMENSIONS, evaluate_assessment
from clipforge.decision.candidates import ROOT, content_hash, generate_candidates
from clipforge.decision.llm import LLMConfig, apply_llm, validate_llm_result
from clipforge.decision.presentation import candidate_page
from clipforge.decision.scoring import score_candidates
from backend.tests.test_candidates import fixture


def assessment(text, scores=(15, 12, 8, 6)):
    return {key: {'score': score, 'finding': 'uncertain', 'quote': text[:8]}
            for key, score in zip(DIMENSIONS, scores)}


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.analysis = fixture([(0,10),(10,20),(20,30)])
        self.rule = score_candidates(generate_candidates(self.analysis), self.analysis)
        cfg = LLMConfig(version='llm-v2', endpoint='https://unused.example/v1/chat/completions', model='fake')
        prompt = (ROOT/'prompts/scoring/llm-v2.txt').read_text(encoding='utf-8')
        self.settings = {'mode':'llm','config':cfg.to_dict(),'prompt':prompt,'prompt_sha256':content_hash(prompt)}
        self.transport = Mock(side_effect=self.respond)

    def respond(self, cfg, payload, key, timeout):
        text = json.loads(payload['messages'][1]['content'])['text']
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(assessment(text),ensure_ascii=False)}}],
                'usage': {'prompt_tokens':100,'completion_tokens':50}}

    def apply(self):
        return apply_llm(self.rule,self.analysis,self.settings,api_key='fake',transport=self.transport,limiter=Mock())

    def test_total_is_computed_not_model_authored(self):
        result = evaluate_assessment(assessment('牵着了原词尚不明确'), '牵着了原词尚不明确')
        self.assertEqual(result['score'],41)
        self.assertEqual(result['reasons'][-1], '总分由程序相加：15 + 12 + 8 + 6 = 41 分。')
        tampered = assessment('原文')
        tampered['score'] = 45
        with self.assertRaises(ValueError): evaluate_assessment(tampered,'原文')

    def test_invented_words_freeform_reasons_bad_types_and_bounds_rejected(self):
        good = assessment('刘慈禧老师原文')
        mutations = [lambda d: d['clarity'].update(quote='刘慈欣'),
                     lambda d: d['clarity'].update(finding='应为刘慈欣'),
                     lambda d: d['clarity'].update(reason='原词应为刘慈欣'),
                     lambda d: d['clarity'].update(score=True),
                     lambda d: d['clarity'].update(score=1.2),
                     lambda d: d['clarity'].update(score=31),
                     lambda d: d['clarity'].update(score=-1),
                     lambda d: d['clarity'].update(score=float('nan')),
                     lambda d: d['clarity'].update(quote=' '),
                     lambda d: d['clarity'].update(quote='原文'*21),
                     lambda d: d.pop('clarity')]
        for mutation in mutations:
            changed = deepcopy(good); mutation(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                evaluate_assessment(changed, '刘慈禧老师原文')

    def test_success_preserves_inputs_exposes_generated_reasons_and_versions(self):
        before = deepcopy(self.rule)
        result = self.apply()
        self.assertEqual(self.rule,before)
        self.assertEqual(result['scoring']['version'],'llm-v2')
        self.assertEqual(result['scoring']['llm']['prompt_version'],'llm-v2')
        self.assertEqual(candidate_page(result,10,0)['scoring_version'],'llm-v2')
        self.assertEqual(candidate_page(result,10,0)['items'][0]['reasons'][-1],
                         '总分由程序相加：15 + 12 + 8 + 6 = 41 分。')
        for c in result['candidates']:
            self.assertEqual(c['score'],41)
            old = next(x for x in before['candidates'] if x['id']==c['id'])
            for key in ('text','start','end','source_sentences','features'):
                self.assertEqual(c[key],old[key])

    def test_artifact_tampering_cannot_make_total_or_reason_disagree(self):
        good = self.apply()
        for mutation in [lambda c:c.update(score=45),lambda c:c.update(reasons=['应为刘慈欣']),
                         lambda c:c['llm_assessment']['clarity'].update(score=11),
                         lambda c:c['llm_assessment']['clarity'].update(quote='不在原文的引用'),
                         lambda c:c.pop('llm_assessment')]:
            bad=deepcopy(good);mutation(bad['candidates'][0])
            with self.assertRaises(ValueError):validate_llm_result(bad,self.analysis)
        bad=deepcopy(good);bad['scoring']['llm']['prompt_version']='llm-v1'
        with self.assertRaises(ValueError):validate_llm_result(bad,self.analysis)

    def test_later_bad_assessment_reverts_all_candidates(self):
        def reply(cfg,payload,key,timeout):
            result = self.respond(cfg,payload,key,timeout)
            if self.transport.call_count == 2:
                result['choices'][0]['message']['content']='{"score":45,"reasons":["old response"]}'
            return result
        self.transport.side_effect=reply
        result=self.apply()
        self.assertEqual(result['scoring']['llm']['fallback_reason'],'invalid_assessment')
        self.assertEqual(self.transport.call_count,2)
        for c in result['candidates']:
            self.assertEqual(c['score'],c['rule_score'])
            self.assertNotIn('llm_assessment',c)
        self.assertEqual(result['scoring']['version'],'rule-v1')

    def test_uncertain_transcription_never_substitutes_a_word(self):
        value=assessment('刘慈禧老师')
        value['clarity']['finding']='transcription_uncertain'
        result=evaluate_assessment(value,'刘慈禧老师')
        self.assertNotIn('刘慈欣',''.join(result['reasons']))
        self.assertIn('需听原音核对',''.join(result['reasons']))


if __name__ == '__main__': unittest.main()
