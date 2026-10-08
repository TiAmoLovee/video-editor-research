"""Opt-in, bounded sentence repair. Original scores and human labels stay separate."""
from copy import deepcopy
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from clipforge.analysis.sentences import _units
from clipforge.decision.candidates import ROOT, content_hash
from clipforge.decision.llm import LLMConfig, LLMFailure, http_transport, strict_json
from clipforge.decision.risk_selection import build_risk_selection
from clipforge.decision.sentence_repair import build_sentence_choices, resolve_selections
from clipforge.storage.jobs import data_root

VERSION = 'sentence-boundary-optimizer-v1'


@dataclass(frozen=True)
class BoundaryConfig:
    endpoint: str = ''
    model: str = 'qwen-plus'
    timeout_seconds: float = 60
    task_timeout_seconds: float = 600
    max_candidates: int = 30
    max_task_tokens: int = 100000
    max_total_tokens: int = 200000
    initial_used_tokens: int = 0

    def __post_init__(self):
        LLMConfig(endpoint=self.endpoint, model=self.model, request_profile='qwen-json',
                  timeout_seconds=self.timeout_seconds, task_timeout_seconds=self.task_timeout_seconds,
                  max_attempts=1, max_candidates=self.max_candidates)
        for key in ('max_task_tokens', 'max_total_tokens', 'initial_used_tokens'):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= 1000000:
                raise ValueError('invalid boundary token limit')
        if not self.max_task_tokens or not self.max_total_tokens or self.initial_used_tokens > self.max_total_tokens:
            raise ValueError('invalid boundary token limits')


def settings_snapshot():
    enabled = os.environ.get('CLIPFORGE_BOUNDARY_REPAIR', '0')
    if enabled == '0':
        return {'mode': 'off'}
    try:
        if enabled != '1' or os.environ.get('CLIPFORGE_SCORER', 'rule') != 'rule':
            raise ValueError('Boundary repair uses its verified rule baseline')
        path = Path(os.environ.get('CLIPFORGE_BOUNDARY_CONFIG', ''))
        cfg = BoundaryConfig(**strict_json(path.read_text(encoding='utf-8-sig')))
        prompts = {name: (ROOT/f'prompts/boundaries/{name}-v1.txt').read_text(encoding='utf-8')
                   for name in ('punctuation', 'selection')}
        return {'mode': 'sentence', 'config': asdict(cfg), 'prompts': prompts,
                'prompts_sha256': content_hash(prompts)}
    except (OSError, ValueError, TypeError, RecursionError):
        return {'mode': 'sentence', 'configuration_error': True}


def compact_packet(plan, analysis, candidate):
    """Preserve all source text and options; reference shared contiguous blocks."""
    units = _units(analysis['words'])
    starts = {u['words'][0]['id']: i for i, u in enumerate(units)}
    ends = {u['words'][-1]['id']: i+1 for i, u in enumerate(units)}
    cuts = {0, len(units)}
    for option in candidate['options']:
        cuts.update((starts[option['first_word_id']], ends[option['last_word_id']]))
    cuts = sorted(cuts)
    blocks, first, last = [], {}, {}
    for i, (lo, hi) in enumerate(zip(cuts, cuts[1:]), 1):
        first[lo], last[hi] = i, i
        blocks.append([i, min(u['start'] for u in units[lo:hi]), max(u['end'] for u in units[lo:hi]),
                       ''.join(u['text'] for u in units[lo:hi])])
    if ''.join(b[3] for b in blocks) != plan['projection']['source_text']:
        raise ValueError('source reconstruction mismatch')
    mapping, groups = {}, defaultdict(list)
    for option in candidate['options']:
        lo, hi = first[starts[option['first_word_id']]], last[ends[option['last_word_id']]]
        key = f'{lo}-{hi}'
        if (key in mapping or ''.join(b[3] for b in blocks[lo-1:hi]) != option['text']
                or blocks[lo-1][1] != option['start'] or blocks[hi-1][2] != option['end']):
            raise ValueError('option reconstruction mismatch')
        mapping[key] = option['id']
        groups[lo].append(hi)
    packet = {'original': {k: candidate['original_text'] if k == 'text' else candidate[k]
                           for k in ('text', 'start', 'end')},
              'source_blocks': blocks,
              'allowed_ranges': ';'.join(f'{lo}:'+','.join(map(str, his)) for lo, his in sorted(groups.items()))}
    return packet, mapping


def decode_choice(answer, packet, mapping):
    if (not isinstance(answer, dict) or set(answer) != {'choice', 'reason'}
            or not isinstance(answer['reason'], str) or not 0 < len(answer['reason']) <= 1000
            or not isinstance(answer['choice'], str)):
        raise ValueError('invalid boundary choice')
    choice = answer['choice']
    pair = re.fullmatch(r'([1-9][0-9]*)\s*[-:：–—]\s*([1-9][0-9]*)', choice.strip())
    if pair:
        choice = pair[1]+'-'+pair[2]
    if choice in ('keep', 'uncertain'):
        return dict(answer, choice=choice)
    if choice in mapping:
        return dict(answer, choice=mapping[choice])
    if pair:
        lo, hi = map(int, (pair[1], pair[2]))
        blocks, original = packet['source_blocks'], packet['original']
        if (1 <= lo <= hi <= len(blocks) and blocks[lo-1][1] == original['start']
                and blocks[hi-1][2] == original['end']
                and ''.join(b[3] for b in blocks[lo-1:hi]) == original['text']):
            return dict(answer, choice='keep')
    raise ValueError('unknown boundary choice')


class BoundedClient:
    def __init__(self, cfg, *, api_key=None, transport=None, ledger=None):
        self.cfg = cfg
        self.key = api_key if api_key is not None else os.environ.get('CLIPFORGE_LLM_API_KEY', '')
        self.transport = transport or http_transport
        self.ledger = Path(ledger) if ledger else data_root()/'boundary-budget.sqlite3'
        self.deadline = time.monotonic()+cfg.task_timeout_seconds
        self.usage = {'requests': 0, 'retries': 0, 'charged_tokens': 0,
                      'prompt_tokens': 0, 'completion_tokens': 0, 'unknown_usage_requests': 0}

    def reserve(self, tokens):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        call = uuid4().hex
        with closing(sqlite3.connect(self.ledger, timeout=5)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, used INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL, charged INTEGER NOT NULL, status TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT used FROM budget WHERE id=1').fetchone()
            if row is None:
                db.execute('INSERT INTO budget VALUES (1,?)', (self.cfg.initial_used_tokens,))
                used = self.cfg.initial_used_tokens
            else:
                used = row[0]
            if db.execute("SELECT 1 FROM calls WHERE status='pending' LIMIT 1").fetchone():
                raise LLMFailure('unknown_usage')
            if used+tokens > self.cfg.max_total_tokens:
                raise LLMFailure('token_budget')
            db.execute('UPDATE budget SET used=used+? WHERE id=1', (tokens,))
            db.execute('INSERT INTO calls VALUES (?,?,?,?)', (call, tokens, tokens, 'pending'))
        return call

    def settle(self, call, reserved, usage, cap):
        keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
        known = (isinstance(usage, dict) and all(type(usage.get(k)) is int and 0 <= usage[k] <= 10000000 for k in keys)
                 and usage['total_tokens'] == usage['prompt_tokens']+usage['completion_tokens'])
        charged = usage['total_tokens'] if known else reserved
        safe = known and charged <= reserved and usage['completion_tokens'] <= cap
        with closing(sqlite3.connect(self.ledger, timeout=5)) as db, db:
            db.execute('UPDATE budget SET used=used+? WHERE id=1', (charged-reserved,))
            db.execute('UPDATE calls SET charged=?,status=? WHERE id=?', (charged, 'settled' if safe else 'pending', call))
        self.usage['charged_tokens'] += charged
        if known:
            self.usage['prompt_tokens'] += usage['prompt_tokens']
            self.usage['completion_tokens'] += usage['completion_tokens']
        if not safe:
            self.usage['unknown_usage_requests'] += 1
            raise LLMFailure('unknown_usage')

    def request(self, system, content, cap):
        cfg = self.cfg
        if not cfg.endpoint or not cfg.model or not self.key or any(ord(c) < 33 or ord(c) > 126 for c in self.key):
            raise LLMFailure('missing_configuration')
        payload = {'model': cfg.model, 'messages': [{'role': 'system', 'content': system},
                   {'role': 'user', 'content': content}], 'stream': False, 'enable_thinking': False,
                   'max_tokens': cap, 'temperature': 0, 'response_format': {'type': 'json_object'}}
        reserved = len(json.dumps(payload, ensure_ascii=False).encode())+cap+4096
        if reserved > 150000 or self.usage['charged_tokens']+reserved > cfg.max_task_tokens:
            raise LLMFailure('token_budget')
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise LLMFailure('task_timeout')
        call = self.reserve(reserved)
        self.usage['requests'] += 1
        response = None
        try:
            config = LLMConfig(endpoint=cfg.endpoint, model=cfg.model, request_profile='qwen-json', max_attempts=1)
            response = self.transport(config, payload, self.key, min(remaining, cfg.timeout_seconds))
        finally:
            self.settle(call, reserved, response.get('usage') if isinstance(response, dict) else None, cap)
        if time.monotonic() >= self.deadline:
            raise LLMFailure('task_timeout')
        try:
            choice = response['choices'][0]
            if choice['finish_reason'] != 'stop' or choice['message'].get('refusal'):
                raise LLMFailure('incomplete_or_refused')
            return strict_json(choice['message']['content'].replace(self.key, '[REDACTED]'))
        except (KeyError, TypeError, IndexError, AttributeError, ValueError, RecursionError) as error:
            raise LLMFailure('invalid_output') from error


def optimize(scored, analysis, snapshot, *, api_key=None, transport=None, ledger=None):
    if snapshot['mode'] == 'off':
        return None
    report = {'version': VERSION, 'artifact_kind': 'boundary_optimization', 'automatic_acceptance': False,
              'analysis_sha256': content_hash(analysis), 'candidates_sha256': content_hash(scored),
              'settings_sha256': content_hash(snapshot), 'source_sha256': analysis['media']['source_sha256'],
              'normalized_sha256': analysis['media']['normalized_sha256'], 'items': [], 'denominator': 0,
              'status': 'fallback', 'fallback_reason': None, 'usage': {'requests': 0, 'retries': 0}}
    client = None
    try:
        if snapshot.get('configuration_error') or snapshot['prompts_sha256'] != content_hash(snapshot['prompts']):
            raise LLMFailure('invalid_configuration')
        from clipforge.decision.scoring import validate_scored
        validate_scored(scored, analysis)
        cfg = BoundaryConfig(**snapshot['config'])
        if scored['scoring']['scorer'] != 'rule':
            raise LLMFailure('rule_baseline_required')
        selection = build_risk_selection(scored, analysis)
        ids = {i['candidate_id'] for i in selection['items'] if i['retained']}
        candidates = sorted((c for c in scored['candidates'] if c['id'] in ids), key=lambda c: c['id'])
        report['denominator'] = len(candidates)
        report['original_candidate_ids'] = [c['id'] for c in candidates]
        if len(candidates) > cfg.max_candidates:
            raise LLMFailure('candidate_limit')
        if not candidates:
            report['status'] = 'ready'
            return report
        client = BoundedClient(cfg, api_key=api_key, transport=transport, ledger=ledger)
        punctuation = client.request(snapshot['prompts']['punctuation'], ''.join(w['text'] for w in analysis['words']), 4096)
        if not isinstance(punctuation, dict) or set(punctuation) != {'punctuated'}:
            raise LLMFailure('invalid_output')
        plan = build_sentence_choices(analysis, candidates, punctuation['punctuated'])
        responses = {}
        for candidate in plan['candidates']:
            packet, mapping = compact_packet(plan, analysis, candidate)
            answer = client.request(snapshot['prompts']['selection'], json.dumps(packet, ensure_ascii=False, separators=(',', ':')), 256)
            responses[candidate['id']] = decode_choice(answer, packet, mapping)
        resolved = resolve_selections(plan, responses)
        for row in resolved['items']:
            original = next(c for c in plan['candidates'] if c['id'] == row['id'])
            row.setdefault('text', original['original_text'])
            row['candidate_id'] = row['id']
            row['original_start'], row['original_end'] = original['start'], original['end']
            row['id'] = content_hash({'source': report['normalized_sha256'], 'candidate': row['candidate_id'],
                                     'first': row['start_frame'], 'last': row['end_frame_exclusive']})
            row['duration_seconds'] = (row['end_frame_exclusive']-row['start_frame'])/30
            if row['decision'] == 'uncertain':
                row['status'] = 'unresolved_model_uncertain'
        report.update(status='ready', items=resolved['items'], projection=plan['projection'])
    except LLMFailure as error:
        report['fallback_reason'] = error.code
    except (ValueError, TypeError, KeyError, IndexError):
        report['fallback_reason'] = 'invalid_boundary_output'
    except (OSError, sqlite3.Error):
        report['fallback_reason'] = 'budget_unavailable'
    finally:
        if client:
            report['usage'] = deepcopy(client.usage)
    return report
