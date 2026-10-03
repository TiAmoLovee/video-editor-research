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
    scorer: Literal['rule']
    scoring_status: Literal['scored']
    reasons: list[str] = Field(min_length=1)
    source_sentences: list[str] = Field(min_length=1)


def candidate_page(data, limit, offset):
    """深度校验在 worker/缓存发布时完成；这里检查头部和当前页的展示字段。"""
    try:
        if (data['schema_version'] != '0.1.0-draft.2' or data['artifact_kind'] != 'scored_candidates'
                or data['stage'] != 'scored' or not isinstance(data['candidates'], list)
                or type(data['candidate_count']) is not int
                or data['candidate_count'] != len(data['candidates'])):
            raise ValueError('候选结果格式无效')
        if data['scoring']['scorer'] != 'rule':
            raise ValueError('不支持的评分器')
        audio_status = data['scoring']['audio']['status']
        if audio_status not in ('measured', 'unavailable', 'no_audio'):
            raise ValueError('音量状态无效')
        items = []
        for index, raw in enumerate(data['candidates'][offset:offset + limit], offset + 1):
            item = CandidateCard.model_validate(raw)
            if item.rank != index or not math.isclose(item.end - item.start, item.duration_seconds, rel_tol=0, abs_tol=1e-8):
                raise ValueError('候选时间或排名不一致')
            items.append(item.model_dump())
        return {'items': items, 'total': data['candidate_count'], 'limit': limit, 'offset': offset,
                'has_more': offset + limit < data['candidate_count'], 'scorer': 'rule',
                'audio_status': audio_status}
    except (KeyError, TypeError, ValidationError) as error:
        raise ValueError('候选结果格式无效') from error
