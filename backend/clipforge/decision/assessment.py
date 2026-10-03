"""有原文引用的分项评审；总分和展示理由由程序生成，不接收自由改词。"""
from copy import deepcopy


DIMENSIONS = {
    'completeness': ('首尾完整性', 40, {
        'clear': '片段有相对明确的起止', 'needs_context': '需要前后文才能理解',
        'incomplete': '语义尚未收束', 'uncertain': '仅凭现有文本难以判断'}),
    'clarity': ('信息清晰度', 30, {
        'clear': '信息表达较清楚', 'unclear_reference': '存在指代不清',
        'transcription_uncertain': '原文存在疑似转写问题，需听原音核对',
        'uncertain': '仅凭现有文本难以判断'}),
    'engagement': ('文本吸引力', 20, {
        'question': '原文的问题可能引发兴趣', 'concrete_detail': '原文包含具体细节',
        'limited': '文本吸引力有限', 'uncertain': '仅凭现有文本难以判断'}),
    'conciseness': ('少重复与赘述', 10, {
        'concise': '表达较精练', 'repetition': '存在重复表达',
        'filler': '存在口语填充', 'uncertain': '仅凭现有文本难以判断'}),
}

ASSESSMENT_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': list(DIMENSIONS),
    'properties': {
        name: {'type': 'object', 'additionalProperties': False,
               'required': ['score', 'finding', 'quote'], 'properties': {
                   'score': {'type': 'integer', 'minimum': 0, 'maximum': spec[1]},
                   'finding': {'type': 'string', 'enum': list(spec[2])},
                   'quote': {'type': 'string', 'minLength': 1, 'maxLength': 40},
               }} for name, spec in DIMENSIONS.items()
    },
}


def evaluate_assessment(value, text):
    """校验枚举及逐字引用；不声称能证明模型判断在语义上正确。"""
    if not isinstance(value, dict) or set(value) != set(DIMENSIONS):
        raise ValueError('分项字段不完整')
    total, reasons = 0, []
    for name, (label, maximum, findings) in DIMENSIONS.items():
        item = value[name]
        if not isinstance(item, dict) or set(item) != {'score', 'finding', 'quote'}:
            raise ValueError('分项结构无效')
        score, finding, quote = item['score'], item['finding'], item['quote']
        if type(score) is not int or not 0 <= score <= maximum:
            raise ValueError('分项分数越界')
        if not isinstance(finding, str) or finding not in findings:
            raise ValueError('未知评审判断')
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 40 or quote not in text:
            raise ValueError('引用不属于原文')
        total += score
        reasons.append(f'{label} {score}/{maximum} 分。模型判断：{findings[finding]}；原文引用：『{quote}』。')
    reasons.append('总分由程序相加：' + ' + '.join(str(value[k]['score']) for k in DIMENSIONS) + f' = {total} 分。')
    return {'score': total, 'reasons': reasons, 'llm_assessment': deepcopy(value)}
