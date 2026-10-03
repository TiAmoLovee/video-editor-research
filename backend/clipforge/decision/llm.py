"""可选 Chat Completions 评分；默认离线，整批降级，不记录密钥或远端错误正文。"""

from copy import deepcopy
from contextlib import closing
from dataclasses import asdict, dataclass
import http.client
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from clipforge.decision.candidates import ROOT, content_hash
from clipforge.decision.scorers import Scorer
from clipforge.storage.jobs import data_root


class LLMFailure(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class LLMConfig:
    version: str = 'llm-v1'
    endpoint: str = ''
    model: str = ''
    request_profile: str = 'json-schema'
    timeout_seconds: float = 15
    task_timeout_seconds: float = 120
    requests_per_minute: int = 30
    max_attempts: int = 2
    max_candidates: int = 30
    max_completion_tokens: int = 512
    max_task_reserved_tokens: int = 60000
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    max_task_cost: float | None = None

    def __post_init__(self):
        if self.version != 'llm-v1':
            raise ValueError('不支持的 LLM 配置版本')
        if self.request_profile not in ('json-schema', 'qwen-json'):
            raise ValueError('不支持的请求配置')
        for key, low, high in [('requests_per_minute',1,600),('max_attempts',1,3),
                               ('max_candidates',1,100),('max_completion_tokens',64,4096),
                               ('max_task_reserved_tokens',1,1000000)]:
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{key} 超出范围')
        for key, high in [('timeout_seconds',60),('task_timeout_seconds',600)]:
            value = getattr(self, key)
            if type(value) not in (int,float) or not math.isfinite(value) or not 0 < value <= high:
                raise ValueError(f'{key} 超出范围')
        for key in ('input_price_per_million','output_price_per_million','max_task_cost'):
            value = getattr(self,key)
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= 1000000):
                raise ValueError(f'{key} 必须是非负有限数或 null')
        if self.max_task_cost is not None and (self.input_price_per_million is None or self.output_price_per_million is None):
            raise ValueError('启用费用限制必须配置输入和输出单价')
        if not isinstance(self.model,str) or len(self.model) > 200 or any(ord(c)<32 for c in self.model):
            raise ValueError('模型名称无效')
        if not isinstance(self.endpoint,str):
            raise ValueError('接口地址无效')
        if self.endpoint:
            url = urlsplit(self.endpoint)
            _ = url.port
            local = url.hostname in ('localhost','127.0.0.1','::1')
            if (url.scheme != 'https' and not (url.scheme == 'http' and local)
                    or not url.hostname or url.username or url.password or url.query or url.fragment
                    or not url.path.endswith('/chat/completions') or any(ord(c)<33 or ord(c)>126 for c in self.endpoint)):
                raise ValueError('接口须为 HTTPS Chat Completions 地址；仅本机测试允许 HTTP，禁止内嵌凭据')

    def to_dict(self):
        return asdict(self)


def settings_snapshot():
    mode = os.environ.get('CLIPFORGE_SCORER','rule')
    if mode not in ('rule','llm'):
        raise ValueError('CLIPFORGE_SCORER 必须是 rule 或 llm')
    if mode == 'rule':
        return {'mode':'rule'}
    path = Path(os.environ.get('CLIPFORGE_LLM_CONFIG') or ROOT/'config/scoring/llm-v1.json')
    try:
        cfg = LLMConfig(**json.loads(path.read_text(encoding='utf-8-sig')))
    except TypeError as error:
        raise ValueError('LLM 配置字段无效') from error
    prompt = (ROOT/'prompts/scoring/llm-v1.txt').read_text(encoding='utf-8')
    return {'mode':'llm','config':cfg.to_dict(),'prompt':prompt,'prompt_sha256':content_hash(prompt)}


def safe_settings_snapshot():
    try:
        return settings_snapshot()
    except (OSError,ValueError,TypeError):
        # 不把异常正文（可能含配置中的凭据）放进下载产物或日志。
        return {'mode':'llm','config':LLMConfig().to_dict(),'prompt':'',
                'prompt_sha256':content_hash(''),'configuration_error':True}


RESPONSE_SCHEMA = {'type':'object','additionalProperties':False,'required':['score','reasons'],
                   'properties':{'score':{'type':'number','minimum':0,'maximum':100},
                                 'reasons':{'type':'array','minItems':1,'maxItems':5,
                                            'items':{'type':'string','minLength':1,'maxLength':400}}}}


def strict_json(text):
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('non-finite JSON')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=bad_constant)


def parse_score(content):
    try:
        result = strict_json(content)
        if not isinstance(result,dict) or set(result) != {'score','reasons'}:
            raise ValueError('fields')
        score,reasons = result['score'],result['reasons']
        if type(score) not in (int,float) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError('score')
        if (not isinstance(reasons,list) or not 1 <= len(reasons) <= 5
                or any(not isinstance(s,str) or not s.strip() or len(s)>400 for s in reasons)):
            raise ValueError('reasons')
        return {'score':round(score,6),'reasons':reasons}
    except (ValueError,TypeError,RecursionError) as error:
        raise LLMFailure('invalid_output') from error


def http_transport(config, payload, api_key, timeout):
    """一个请求，无自动重试或重定向；远端内容不写日志。"""
    url = urlsplit(config.endpoint)
    connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(url.hostname,url.port,timeout=timeout)
    deadline = time.monotonic()+timeout
    try:
        connection.request('POST',url.path,body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8'),
                           headers={'Content-Type':'application/json','Authorization':'Bearer '+api_key})
        response = connection.getresponse()
        if response.status != 200:
            code = 'invalid_key' if response.status == 401 else 'access_denied' if response.status == 403 else 'rate_limited' if response.status == 429 else 'provider_error'
            if response.status == 403:
                # 只识别固定错误码；不记录可能含凭据的服务商正文。
                try:
                    body = response.read(8193)
                    error = strict_json(body.decode('utf-8')) if len(body) <= 8192 else {}
                    if isinstance(error, dict) and isinstance(error.get('error'), dict):
                        if error['error'].get('code') == 'AllocationQuota.FreeTierOnly':
                            code = 'free_quota_exhausted'
                except (ValueError, UnicodeError, RecursionError):
                    pass
            raise LLMFailure(code,response.status == 429 or 500 <= response.status < 600)
        parts,total = [],0
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise LLMFailure('timeout',True)
            if connection.sock:
                connection.sock.settimeout(remaining)
            block = response.read1(16384)
            if not block:
                break
            total += len(block)
            if total > 262144:
                raise LLMFailure('response_too_large')
            parts.append(block)
        try:
            result = strict_json(b''.join(parts).decode('utf-8'))
        except (ValueError,UnicodeError,RecursionError) as error:
            raise LLMFailure('invalid_response') from error
        return result
    except TimeoutError as error:
        raise LLMFailure('timeout',True) from error
    except (OSError,http.client.HTTPException) as error:
        raise LLMFailure('network_error',True) from error
    finally:
        connection.close()


def rate_slot(config, deadline):
    """共享数据目录内跨 worker 限速；仅存时间，不存密钥或候选。"""
    root = data_root(); root.mkdir(parents=True,exist_ok=True)
    try:
        with closing(sqlite3.connect(root/'llm-rate.sqlite3',timeout=1)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS rate (id INTEGER PRIMARY KEY, next REAL NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT next FROM rate WHERE id=1').fetchone()
            now = time.time()
            slot = max(now,row[0] if row else now)
            delay = slot-now
            if delay >= deadline-time.monotonic():
                raise LLMFailure('task_timeout')
            db.execute('INSERT OR REPLACE INTO rate VALUES (1,?)',(slot+60/config.requests_per_minute,))
        if delay:
            time.sleep(delay)
    except (OSError,sqlite3.Error) as error:
        raise LLMFailure('rate_limiter_unavailable') from error


class LLMScorer(Scorer):
    name,version = 'llm','llm-v1'

    def __init__(self, config, prompt, api_key=None, transport=None, limiter=None):
        self.config,self.prompt = config,prompt
        self.api_key = api_key if api_key is not None else os.environ.get('CLIPFORGE_LLM_API_KEY','')
        self.transport,self.limiter = transport or http_transport,limiter or rate_slot
        self.deadline = time.monotonic()+config.task_timeout_seconds
        self.usage = {'requests':0,'retries':0,'prompt_tokens':0,'completion_tokens':0,
                      'unknown_usage_requests':0,'reserved_tokens':0,'reserved_cost':0.0,
                      'estimated_known_cost':0.0,'currency':'configured','events':[]}

    def score(self, candidate, analysis, audio=None):
        cfg = self.config
        if not cfg.endpoint or not cfg.model:
            raise LLMFailure('missing_configuration')
        if not self.api_key or any(ord(c)<33 or ord(c)>126 for c in self.api_key):
            raise LLMFailure('missing_key')
        content = json.dumps({key:candidate[key] for key in ('id','text','start','end','duration_seconds','source_sentences')},ensure_ascii=False)
        if len(content.encode('utf-8')) > 16000:
            raise LLMFailure('candidate_too_large')
        payload = {'model':cfg.model,'messages':[{'role':'system','content':self.prompt},{'role':'user','content':content}],
                   'stream':False,'max_completion_tokens':cfg.max_completion_tokens,
                   'response_format':{'type':'json_schema','json_schema':{'name':'clip_score','strict':True,'schema':RESPONSE_SCHEMA}}}
        if cfg.request_profile == 'qwen-json':
            # qwen-plus 非思考模式支持 JSON Object；结构仍由 parse_score 严格校验。
            payload.pop('max_completion_tokens')
            payload.update(max_tokens=cfg.max_completion_tokens, enable_thinking=False,
                           response_format={'type':'json_object'})
        # UTF-8 字节数加协议余量是本地保守预留，不冒充服务商实际 token 计数。
        input_reserve = len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))+1024
        reserved = input_reserve+cfg.max_completion_tokens
        priced = cfg.input_price_per_million is not None and cfg.output_price_per_million is not None
        reserve_cost = ((input_reserve*cfg.input_price_per_million+cfg.max_completion_tokens*cfg.output_price_per_million)/1e6 if priced else 0)
        for attempt in range(cfg.max_attempts):
            if time.monotonic() >= self.deadline:
                raise LLMFailure('task_timeout')
            if self.usage['reserved_tokens']+reserved > cfg.max_task_reserved_tokens:
                raise LLMFailure('token_budget')
            if cfg.max_task_cost is not None and self.usage['reserved_cost']+reserve_cost > cfg.max_task_cost:
                raise LLMFailure('cost_budget')
            self.limiter(cfg,self.deadline)
            remaining = self.deadline-time.monotonic()
            if remaining <= 0:
                raise LLMFailure('task_timeout')
            self.usage['reserved_tokens'] += reserved
            self.usage['reserved_cost'] += reserve_cost
            self.usage['requests'] += 1
            self.usage['retries'] += int(attempt>0)
            event = {'candidate_id':candidate['id'],'attempt':attempt+1,'status':'unknown'}
            self.usage['events'].append(event)
            usage_known = False
            try:
                response = self.transport(cfg,payload,self.api_key,min(cfg.timeout_seconds,remaining))
                usage = response.get('usage') if isinstance(response,dict) else None
                if isinstance(usage,dict) and all(type(usage.get(k)) is int and 0<=usage[k]<=10000000 for k in ('prompt_tokens','completion_tokens')):
                    usage_known = True
                    self.usage['prompt_tokens'] += usage['prompt_tokens']
                    self.usage['completion_tokens'] += usage['completion_tokens']
                    self.usage['reserved_tokens'] += max(0,usage['prompt_tokens']+usage['completion_tokens']-reserved)
                    if priced:
                        actual_cost = (usage['prompt_tokens']*cfg.input_price_per_million+usage['completion_tokens']*cfg.output_price_per_million)/1e6
                        self.usage['estimated_known_cost'] += actual_cost
                        self.usage['reserved_cost'] += max(0,actual_cost-reserve_cost)
                if time.monotonic() >= self.deadline:
                    raise LLMFailure('task_timeout')
                choice = response['choices'][0]
                if choice['finish_reason'] != 'stop' or choice['message'].get('refusal'):
                    raise LLMFailure('incomplete_or_refused')
                result = parse_score(choice['message']['content'])
                event['status'] = 'ok'
                return {**result,'features':{}}
            except (KeyError,TypeError,IndexError,AttributeError) as error:
                event['status'] = 'invalid_response'
                raise LLMFailure('invalid_response') from error
            except LLMFailure as error:
                event['status'] = error.code
                if not error.retryable or attempt+1 == cfg.max_attempts:
                    raise
                pause = min(2**attempt,4)
                if time.monotonic()+pause >= self.deadline:
                    raise LLMFailure('task_timeout') from error
                time.sleep(pause)
            finally:
                if not usage_known:
                    self.usage['unknown_usage_requests'] += 1

    def report(self):
        result = deepcopy(self.usage)
        priced = self.config.input_price_per_million is not None and self.config.output_price_per_million is not None
        if not priced:
            result['estimated_known_cost'] = result['reserved_cost'] = None
        if not result['requests']:
            result['estimated_known_cost'] = result['reserved_cost'] = 0.0
        result['total_cost_known'] = not result['requests'] or (priced and result['unknown_usage_requests'] == 0)
        result['cost_note'] = 'configured rates estimate; unknown requests may be billed; reservation is not provider billing'
        return result


def apply_llm(rule, analysis, snapshot, *, api_key=None, transport=None, limiter=None):
    """整批成功才使用 LLM 分数；原规则结果完整保留以供对比和降级。"""
    from clipforge.decision.scorers import default_registry
    from clipforge.decision.scoring import validate_scored, window_order
    validate_scored(rule,analysis)
    cfg = LLMConfig(**snapshot['config'])
    scorer = default_registry().create('llm',config=cfg,prompt=snapshot['prompt'],api_key=api_key,transport=transport,limiter=limiter)
    result = deepcopy(rule)
    result['schema_version'] = '0.1.0-draft.3'
    fallback = None
    responses = {}
    try:
        if snapshot.get('configuration_error') or snapshot['prompt_sha256'] != content_hash(snapshot['prompt']):
            raise LLMFailure('invalid_configuration')
        if len(rule['candidates']) > cfg.max_candidates:
            raise LLMFailure('candidate_limit')
        for candidate in sorted(rule['candidates'],key=window_order):
            responses[candidate['id']] = scorer.score(candidate,analysis)
    except LLMFailure as error:
        fallback = error.code
    effective = 'rule' if fallback or not rule['candidates'] else 'llm'
    result['scoring']['scorer'] = effective
    result['scoring']['version'] = 'llm-v1' if effective == 'llm' else 'rule-v1'
    result['scoring']['llm'] = {'requested':'llm','effective':effective,'fallback_reason':fallback,
                              'config':cfg.to_dict(),'prompt_version':'llm-v1',
                              'prompt_sha256':snapshot['prompt_sha256'],'usage':scorer.report()}
    for candidate in result['candidates']:
        candidate.update(rule_score=candidate['score'],rule_reasons=deepcopy(candidate['reasons']),rule_rank=candidate['rank'])
        if effective == 'llm':
            response = responses[candidate['id']]
            candidate.update(score=response['score'],reasons=response['reasons'],scorer='llm')
    result['candidates'].sort(key=lambda c:(-c['score'],*window_order(c)))
    for rank,candidate in enumerate(result['candidates'],1):
        candidate['rank'] = rank
    validate_llm_result(result,analysis)
    return result


def validate_llm_result(data, analysis):
    from jsonschema import Draft202012Validator
    from clipforge.analysis.validation import _finite
    from clipforge.decision.scoring import validate_scored, window_order
    _finite(data)
    schema = json.loads((ROOT/'schemas/llm_candidates.schema.json').read_text(encoding='utf-8'))
    error = next(Draft202012Validator(schema).iter_errors(data),None)
    if error:
        raise ValueError('LLM 评分产物结构无效')
    metadata = data['scoring']['llm']
    LLMConfig(**metadata['config'])
    effective = metadata['effective']
    if (data['scoring']['scorer'] != effective or data['scoring']['version'] != effective+'-v1'
            or (effective == 'llm' and metadata['fallback_reason'] is not None)):
        raise ValueError('LLM 评分模式不一致')
    if effective == 'rule' and data['candidate_count'] and metadata['fallback_reason'] is None:
        raise ValueError('缺少降级原因')
    if data['candidates'] != sorted(data['candidates'],key=lambda c:(-c['score'],*window_order(c))):
        raise ValueError('LLM 评分未正确排序')
    baseline = deepcopy(data)
    baseline['schema_version'] = '0.1.0-draft.2'
    baseline['scoring'].pop('llm')
    baseline['scoring']['scorer'] = 'rule'
    baseline['scoring']['version'] = 'rule-v1'
    for rank,candidate in enumerate(baseline['candidates'],1):
        if candidate['rank'] != rank or candidate['scorer'] != effective:
            raise ValueError('排名或评分器不一致')
        if effective == 'rule' and (candidate['score'] != candidate['rule_score'] or candidate['reasons'] != candidate['rule_reasons']):
            raise ValueError('降级未保留规则结果')
        candidate.update(score=candidate.pop('rule_score'),reasons=candidate.pop('rule_reasons'),rank=candidate.pop('rule_rank'),scorer='rule')
    baseline['candidates'].sort(key=lambda c:c['rank'])
    validate_scored(baseline,analysis)
