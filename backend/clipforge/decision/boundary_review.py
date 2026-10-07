"""Prepare auditable, offline text-context requests and validate model proposals.

No key access, network calls, score changes or automatic acceptance here.
"""
from copy import deepcopy
import json

from clipforge.decision.candidates import ROOT, content_hash
from clipforge.decision.llm import strict_json
from clipforge.decision.scoring import validate_scored
from clipforge.decision.continuity import tail_signal

VERSION = 'boundary-context-review-v2'
VERSIONS = ('boundary-context-review-v1', VERSION)
ENDPOINT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
MODEL = 'qwen-plus'


class ReviewValidationError(ValueError):
    """Fixed diagnostic codes, never provider text or credential-bearing errors."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def prepare_review(analysis, scored, candidate_id, *, version=VERSION):
    if version not in VERSIONS:
        raise ValueError('复核协议版本无效')
    validate_scored(scored, analysis)
    candidate = next((c for c in scored['candidates'] if c['id'] == candidate_id), None)
    if candidate is None:
        raise ValueError('候选不存在')
    sentences = sorted(analysis['sentences'], key=lambda s: (s['start'], s['end'], s['id']))
    by_id = {s['id']: s for s in sentences}
    refs = candidate['source_sentences']
    options = [c for c in scored['candidates']
               if c['start'] == candidate['start'] and c['end'] > candidate['end']
               and c['source_sentences'][:len(refs)] == refs
               and all(0 <= by_id[right]['start']-by_id[left]['end'] <= 3
                       for left, right in zip(c['source_sentences'], c['source_sentences'][1:]))]
    excluded = []
    if version == VERSION:
        for option in options:
            signal = tail_signal(by_id[option['source_sentences'][-1]]['text'])
            if signal:
                excluded.append({'candidate_id':option['id'], 'start':option['start'], 'end':option['end'],
                                 'signal':signal, 'scope':'excluded from model suggestions only; original candidate unchanged'})
        excluded_ids = {item['candidate_id'] for item in excluded}
        options = [c for c in options if c['id'] not in excluded_ids]
    options = sorted(options, key=lambda c: (c['end'], c['id']))[:5]
    before = [s for s in sentences if s['end'] <= candidate['start'] and s['id'] not in refs][-2:]
    after = [s for s in sentences if s['start'] >= candidate['end'] and s['id'] not in refs][:2]
    included = set(refs) | {s['id'] for s in before+after}
    included.update(sid for c in options for sid in c['source_sentences'])
    fields = ('id', 'start', 'end', 'source_sentences')
    inputs = {'candidate': {k: deepcopy(candidate[k]) for k in fields},
              'allowed_alternatives': [{k: deepcopy(c[k]) for k in fields} for c in options],
              'sentences': [{k: deepcopy(s[k]) for k in ('id', 'start', 'end', 'text')}
                            for s in sentences if s['id'] in included],
              'limitations': 'ASR text only; no video, audio or human labels; suggestions need separate audition'}
    prompt_name = 'context-v1.txt' if version == VERSIONS[0] else 'context-v2.txt'
    prompt = (ROOT/'prompts/boundary'/prompt_name).read_text(encoding='utf-8')
    payload = {'model': MODEL, 'temperature': 0, 'max_tokens': 512,
               'enable_thinking': False, 'stream': False,
               'response_format': {'type': 'json_object'},
               'messages': [{'role':'system', 'content':prompt},
                            {'role':'user', 'content':json.dumps(inputs, ensure_ascii=False, separators=(',', ':'))}]}
    plan = {'version': version, 'endpoint': ENDPOINT, 'payload': payload,
            'input': inputs, 'analysis_sha256':content_hash(analysis),
            'candidates_sha256':content_hash(scored), 'prompt_sha256':content_hash(prompt),
            'max_requests':1, 'max_attempts':1, 'requires_new_user_authorization':True}
    if version == VERSION:
        plan['excluded_alternatives'] = excluded
    return {'plan_sha256':content_hash(plan), **plan}


def check_plan(plan):
    expected = {'version','endpoint','payload','input','analysis_sha256','candidates_sha256',
                'prompt_sha256','max_requests','max_attempts','requires_new_user_authorization','plan_sha256'}
    if isinstance(plan, dict) and plan.get('version') == VERSION:
        expected.add('excluded_alternatives')
    if (not isinstance(plan, dict) or set(plan) != expected or plan['version'] not in VERSIONS
            or content_hash({k:v for k,v in plan.items() if k != 'plan_sha256'}) != plan['plan_sha256']):
        raise ValueError('冻结请求已变化')
    payload = plan['payload']
    if (plan['endpoint'] != ENDPOINT or plan['max_requests'] != 1 or plan['max_attempts'] != 1
            or payload['model'] != MODEL or payload['max_tokens'] != 512
            or payload.get('stream') is not False or payload.get('enable_thinking') is not False):
        raise ValueError('请求目的地或调用上限已变化')
    messages = payload['messages']
    if (len(messages) != 2 or messages[0]['role'] != 'system' or messages[1]['role'] != 'user'
            or content_hash(messages[0]['content']) != plan['prompt_sha256']
            or strict_json(messages[1]['content']) != plan['input']):
        raise ValueError('实际消息与审阅文本不一致')


def validate_review(content, plan):
    """Verify syntax, exact citations and allow-listed ranges, not semantic truth."""
    check_plan(plan)
    try:
        result = strict_json(content)
    except (ValueError, TypeError, RecursionError) as error:
        raise ReviewValidationError('invalid_json') from error
    keys = {'ending_status', 'reason', 'evidence', 'recommended_candidate_id'}
    if (not isinstance(result, dict) or set(result) != keys
            or result['ending_status'] not in ('complete','incomplete','uncertain')
            or not isinstance(result['reason'], str) or not 1 <= len(result['reason'].strip()) <= 200
            or not isinstance(result['evidence'], list) or not 1 <= len(result['evidence']) <= 3):
        raise ReviewValidationError('invalid_assessment_fields')
    inputs = plan['input']
    by_id = {s['id']:s for s in inputs['sentences']}
    cited = set()
    for evidence in result['evidence']:
        if (not isinstance(evidence, dict) or set(evidence) != {'sentence_id','quote'}
                or not isinstance(evidence['sentence_id'], str) or evidence['sentence_id'] not in by_id
                or not isinstance(evidence['quote'], str) or not 2 <= len(evidence['quote'].strip()) <= 80
                or evidence['quote'] not in by_id[evidence['sentence_id']]['text']):
            raise ReviewValidationError('invalid_source_citation')
        cited.add(evidence['sentence_id'])
    refs = set(inputs['candidate']['source_sentences'])
    if not cited.intersection(refs):
        raise ReviewValidationError('candidate_citation_missing')
    chosen = result['recommended_candidate_id']
    if chosen is not None:
        if not isinstance(chosen, str) or result['ending_status'] == 'complete':
            raise ReviewValidationError('recommendation_status_conflict')
        option = next((c for c in inputs['allowed_alternatives'] if c['id'] == chosen), None)
        if option is None:
            raise ReviewValidationError('recommendation_not_allowed')
        if not cited.intersection(set(option['source_sentences'])-refs):
            raise ReviewValidationError('extension_citation_missing')
    return {'version':plan['version'],'plan_sha256':plan['plan_sha256'],'assessment':deepcopy(result),
            'status':'format_and_citations_verified','semantic_quality':'requires_review',
            'automatic_acceptance':False,'scores_and_ranges_changed':False}
