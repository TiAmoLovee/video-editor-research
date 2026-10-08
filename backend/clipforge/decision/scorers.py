"""可注册的评分接口及可解释规则基线；不在这里调用外部模型。"""

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import re


class Scorer(ABC):
    name: str
    version: str

    @abstractmethod
    def score(self, candidate, analysis, audio=None):
        """返回 score、reasons 和 features；不改变候选时间或文字。"""


class ScorerRegistry:
    def __init__(self):
        self._factories = {}

    def register(self, name, factory):
        if not isinstance(name, str) or not name.strip() or not callable(factory):
            raise ValueError('评分器名称及工厂无效')
        if name in self._factories:
            raise ValueError(f'评分器已注册：{name}')
        self._factories[name] = factory

    def create(self, name, **kwargs):
        if name not in self._factories:
            raise ValueError(f'未知评分器：{name}')
        scorer = self._factories[name](**kwargs)
        if not isinstance(scorer, Scorer) or scorer.name != name:
            raise ValueError('注册工厂必须返回名称匹配的 Scorer')
        return scorer


@dataclass(frozen=True)
class RuleConfig:
    version: str = 'rule-v1'
    keywords: tuple = ('关键', '原因', '方法', '总结', '注意', '为什么', '怎么')
    base_score: float = 20.0
    keyword_weight: float = 35.0
    pace_weight: float = 15.0
    volume_weight: float = 15.0
    qa_weight: float = 15.0
    duration_penalty_weight: float = 20.0
    keyword_density_target: float = 0.08
    pace_change_target: float = 0.5
    volume_prominence_db: float = 12.0
    target_seconds: float = 30.0

    def __post_init__(self):
        if self.version != 'rule-v1':
            raise ValueError('不支持的规则配置版本')
        if not isinstance(self.keywords, (tuple, list)) or len(self.keywords) > 100:
            raise ValueError('keywords 必须是最多 100 项的列表')
        if any(not isinstance(k, str) or not k.strip() or len(k) > 64 for k in self.keywords):
            raise ValueError('关键词不能为空或超过 64 字符')
        normalized = tuple(k.strip().casefold() for k in self.keywords)
        if len(set(normalized)) != len(normalized):
            raise ValueError('关键词重复')
        object.__setattr__(self, 'keywords', normalized)
        for key, value in asdict(self).items():
            if key in ('version', 'keywords'):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} 必须是非负有限数')
        if not 15 <= self.target_seconds <= 90:
            raise ValueError('目标时长须在 15–90 秒之间')
        if not 0 < self.keyword_density_target <= 1 or self.pace_change_target <= 0 or self.volume_prominence_db <= 0:
            raise ValueError('归一化阈值必须大于零，关键词密度阈值不得超过 1')
        if self.base_score + self.keyword_weight + self.pace_weight + self.volume_weight + self.qa_weight > 100:
            raise ValueError('基础分与奖励权重总和不得超过 100')
        if self.duration_penalty_weight > 100:
            raise ValueError('时长惩罚不得超过 100 分')

    def to_dict(self):
        result = asdict(self)
        result['keywords'] = list(self.keywords)
        return result


def text_units(text):
    # 中文及其他字母数字按字符计数；不用 Whisper token 数作为中文词数。
    return sum(character.isalnum() for character in text)


QUESTION = re.compile(r'[?？]|为什么|怎么|如何|是否|什么')
ANSWER = re.compile(r'因为|所以|答案|也就是说|可以|不能|首先|是这样')


class RuleScorer(Scorer):
    name = 'rule'
    version = 'rule-v1'

    def __init__(self, config=None):
        self.config = config or RuleConfig()

    def score(self, candidate, analysis, audio=None):
        cfg = self.config
        sentences = {s['id']: s for s in analysis['sentences']}
        members = [sentences[key] for key in candidate['source_sentences']]
        text = candidate['text'].casefold()
        units = text_units(text)
        # 从左到右最长优先匹配；重叠关键词不重复计数。
        pattern = '|'.join(re.escape(k) for k in sorted(cfg.keywords, key=lambda k: (-len(k), k)))
        hits = [m.group() for m in re.finditer(pattern, text)] if pattern else []
        coverage = sum(text_units(hit) for hit in hits)
        density = coverage / units if units else 0
        keyword_value = min(1, density / cfg.keyword_density_target)
        rates = [text_units(s['text']) / (s['end'] - s['start']) for s in members]
        mean_rate = sum(rates) / len(rates)
        adjacent_change = (sum(abs(a-b) for a, b in zip(rates, rates[1:])) / (len(rates)-1)
                           if len(rates) > 1 else 0)
        relative_change = adjacent_change / mean_rate if mean_rate else 0
        pace_value = min(1, relative_change / cfg.pace_change_target)
        qa_pairs = []
        for index, sentence in enumerate(members):
            match = QUESTION.search(sentence['text'])
            if not match:
                continue
            for other in members[index:index+3]:
                answer_text = other['text'][match.end():] if other is sentence else other['text']
                if ANSWER.search(answer_text):
                    qa_pairs.append([sentence['id'], other['id']])
                    break
        qa_value = 1.0 if qa_pairs else 0.0
        if audio is None:
            volume_value = 0.0
            volume_evidence = {'status': 'unavailable' if analysis['media']['has_audio'] else 'no_audio'}
        else:
            if audio.normalized_sha256 != analysis['media']['normalized_sha256'] or not math.isclose(
                    audio.duration_seconds, analysis['media']['duration_seconds'], rel_tol=0, abs_tol=1e-6):
                raise ValueError('音量特征与分析来源不匹配')
            volume_evidence = {'status': 'measured', **audio.window(candidate['start'], candidate['end'])}
            peak = volume_evidence['peak_rms_dbfs']
            prominence = max(0, peak - audio.baseline_dbfs) if peak > -60 and audio.baseline_dbfs > -60 else 0
            volume_evidence['prominence_db'] = round(prominence, 6)
            volume_value = min(1, prominence / cfg.volume_prominence_db)
        penalty = min(1, abs(candidate['duration_seconds'] - cfg.target_seconds) / cfg.target_seconds)
        def feature(value, weight, evidence):
            return {'value': round(value, 8), 'contribution': round(value * weight, 6), 'evidence': evidence}
        features = {
            'keyword_density': feature(keyword_value, cfg.keyword_weight,
                {'hits': hits, 'covered_units': coverage, 'text_units': units, 'density': density}),
            'pace_change': feature(pace_value, cfg.pace_weight,
                {'unit': 'alphanumeric characters per second', 'sentence_rates': rates,
                 'relative_adjacent_change': relative_change}),
            'volume_peak': feature(volume_value, cfg.volume_weight, volume_evidence),
            'qa_pattern': feature(qa_value, cfg.qa_weight, {'matched_sentence_pairs': qa_pairs}),
            'duration_penalty': feature(penalty, -cfg.duration_penalty_weight,
                {'duration_seconds': candidate['duration_seconds'], 'target_seconds': cfg.target_seconds}),
        }
        reasons = [f'关键词覆盖 {density:.1%}（{len(hits)} 次匹配），贡献 {features["keyword_density"]["contribution"]:+.2f} 分。',
                   f'相邻句语速相对变化 {relative_change:.1%}，贡献 {features["pace_change"]["contribution"]:+.2f} 分。']
        if audio is None:
            reasons.append('音量证据不可用，此项记 0 分，其他权重不变。')
        else:
            reasons.append(f'100ms 音量峰值 {volume_evidence["peak_rms_dbfs"]:.2f} dBFS，贡献 {features["volume_peak"]["contribution"]:+.2f} 分。')
        reasons += [f'问答提示词模式匹配 {len(qa_pairs)} 对，贡献 {features["qa_pattern"]["contribution"]:+.2f} 分；不等同于语义理解。',
                    f'时长 {candidate["duration_seconds"]:.2f} 秒，目标 {cfg.target_seconds:g} 秒，扣除 {-features["duration_penalty"]["contribution"]:.2f} 分。']
        score = round(max(0, min(100, cfg.base_score + sum(f['contribution'] for f in features.values()))), 6)
        return {'score': score, 'reasons': reasons, 'features': deepcopy(features)}


def default_registry():
    from clipforge.decision.llm import LLMScorer
    registry = ScorerRegistry()
    registry.register('rule', RuleScorer)
    registry.register('llm', LLMScorer)
    return registry
