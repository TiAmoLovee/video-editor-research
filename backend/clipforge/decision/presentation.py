"""已验证产物的分页展示投影；不在 HTTP 请求中重算评分。"""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CandidateCard(BaseModel):
    model_config = ConfigDict(extra='ignore', allow_inf_nan=False, strict=True)
    id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    duration_seconds: float = Field(ge=15, le=90)
    text: str = Field(min_length=1)
    score: float = Field(ge=0, le=100)
    scorer: Literal['rule', 'llm']
    scoring_status: Literal['scored']
    reasons: list[str] = Field(min_length=1)
    source_sentences: list[str] = Field(min_length=1)
    rule_score: float | None = Field(default=None, ge=0, le=100)


def candidate_page(data, limit, offset):
    """深度校验在 worker/缓存发布时完成；这里检查头部和当前页的展示字段。"""
    try:
        if (data['schema_version'] not in ('0.1.0-draft.2','0.1.0-draft.3') or data['artifact_kind'] != 'scored_candidates'
                or data['stage'] != 'scored' or not isinstance(data['candidates'], list)
                or type(data['candidate_count']) is not int
                or data['candidate_count'] != len(data['candidates'])):
            raise ValueError('候选结果格式无效')
        scorer = data['scoring']['scorer']
        if scorer not in ('rule','llm'):
            raise ValueError('不支持的评分器')
        scoring_version = data['scoring'].get('version')
        if scoring_version not in ({'rule-v1'} if scorer == 'rule' else {'llm-v1', 'llm-v2'}):
            raise ValueError('评分版本与实际评分器不一致')
        llm = data['scoring'].get('llm')
        if data['schema_version'] == '0.1.0-draft.3':
            if not isinstance(llm,dict) or llm.get('effective') != scorer or llm.get('requested') != 'llm':
                raise ValueError('模型评分状态无效')
        elif scorer != 'rule' or llm:
            raise ValueError('旧版只支持规则评分')
        audio_status = data['scoring']['audio']['status']
        if audio_status not in ('measured', 'unavailable', 'no_audio'):
            raise ValueError('音量状态无效')
        items = []
        for index, raw in enumerate(data['candidates'][offset:offset + limit], offset + 1):
            item = CandidateCard.model_validate(raw)
            if item.scorer != scorer or item.rank != index or not math.isclose(item.end - item.start, item.duration_seconds, rel_tol=0, abs_tol=1e-8):
                raise ValueError('候选时间或排名不一致')
            items.append(item.model_dump())
        return {'items': items, 'total': data['candidate_count'], 'limit': limit, 'offset': offset,
                'has_more': offset + limit < data['candidate_count'], 'scorer': scorer,
                'scoring_version': scoring_version,
                'requested_scorer':'llm' if llm else 'rule',
                'fallback_reason':llm.get('fallback_reason') if llm else None,
                'audio_status': audio_status}
    except (KeyError, TypeError, ValidationError) as error:
        raise ValueError('候选结果格式无效') from error
