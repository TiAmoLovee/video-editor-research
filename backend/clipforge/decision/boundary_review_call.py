"""Explicit one-attempt execution of an already reviewed request. Not a worker path."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from clipforge.decision.boundary_review import check_plan, validate_review
from clipforge.decision.llm import LLMConfig, LLMFailure, http_transport


def execute_batch(plans, api_key, approved_hashes, output_dir, *, transport=None):
    """At most two individually authorized requests; stop on the first failure."""
    if not 1 <= len(plans) <= 2 or len(approved_hashes) != len(plans):
        raise ValueError('本次仅允许一至两个已授权请求')
    for plan, approved in zip(plans, approved_hashes):
        check_plan(plan)
        if approved != plan['plan_sha256']:
            raise ValueError('请求与授权不一致')
    if len(set(approved_hashes)) != len(plans):
        raise ValueError('不允许重复请求')
    results = []
    for plan, approved in zip(plans, approved_hashes):
        result = execute_once(plan, api_key, approved, output_dir, transport=transport)
        results.append(result)
        if result['status'] != 'format_and_citations_verified':
            break
    return results


def execute_once(plan, api_key, approved_plan_sha256, output_dir, *, transport=None):
    """Caller must obtain actual user authorization; hash binds its exact payload.

    An attempt receipt is saved before sending. Unknown outcomes are not retried.
    No defaults read credentials, no automatic acceptance or score changes.
    """
    check_plan(plan)
    if approved_plan_sha256 != plan['plan_sha256']:
        raise ValueError('授权哈希与请求不一致')
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError('未提供调用密钥')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = plan['plan_sha256']
    cfg = LLMConfig(endpoint=plan['endpoint'], model=plan['payload']['model'],
                    request_profile='qwen-json', timeout_seconds=45, max_attempts=1,
                    max_candidates=1, max_completion_tokens=512)
    attempt = {'plan_sha256':stem, 'started_at':datetime.now(timezone.utc).isoformat(),
               'requests_reserved':1, 'retries':0, 'outcome':'unknown_until_result_saved'}
    with (output_dir/f'{stem}.attempt.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(attempt, ensure_ascii=False, indent=2)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    result = {'plan_sha256':stem, 'requests':1, 'retries':0, 'usage':None,
              'automatic_acceptance':False, 'scores_and_ranges_changed':False}
    try:
        response = (transport or http_transport)(cfg, plan['payload'], api_key, cfg.timeout_seconds)
        usage = response.get('usage')
        if isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k]>=0
                                          for k in ('prompt_tokens','completion_tokens')):
            result['usage'] = {k:usage[k] for k in ('prompt_tokens','completion_tokens')}
        if len(response['choices']) != 1:
            raise ValueError('invalid_choice_count')
        choice = response['choices'][0]
        if choice['finish_reason'] != 'stop' or choice['message'].get('refusal'):
            raise LLMFailure('incomplete_or_refused')
        result.update(validate_review(choice['message']['content'], plan))
    except LLMFailure as error:
        known = {'invalid_key','access_denied','rate_limited','provider_error','free_quota_exhausted',
                 'timeout','network_error','response_too_large','invalid_response','incomplete_or_refused'}
        result.update(status='failed', error=error.code if error.code in known else 'provider_error')
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        result.update(status='failed', error='invalid_output')
    with (output_dir/f'{stem}.result.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result
