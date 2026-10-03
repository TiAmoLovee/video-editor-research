"""离线规则评分命令：候选、分项证据、确定性排序与可复跑配置。"""

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from clipforge.analysis.validation import _finite
from clipforge.decision.audio import measure_audio
from clipforge.decision.candidates import ROOT, content_hash
from clipforge.decision.scorers import RuleConfig, default_registry
from clipforge.decision.validation import validate_candidates


def load_config(path=None):
    path = Path(path) if path else ROOT / 'config/scoring/rule-v1.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('规则配置必须是 JSON 对象')
    try:
        return RuleConfig(**data)
    except TypeError as error:
        raise ValueError(f'规则配置字段无效：{error}') from error


def window_order(candidate):
    return candidate['start'], candidate['end'], candidate['id']


def score_candidates(windows, analysis, *, config=None, audio=None):
    validate_candidates(windows, analysis)
    if windows['candidates'] != sorted(windows['candidates'], key=window_order):
        raise ValueError('输入候选必须按时间顺序排列')
    config = config or load_config()
    scorer = default_registry().create('rule', config=config)
    if audio is not None and (audio.normalized_sha256 != analysis['media']['normalized_sha256']
                             or not math.isclose(audio.duration_seconds, analysis['media']['duration_seconds'], abs_tol=1e-6, rel_tol=0)):
        raise ValueError('音量特征与分析来源不匹配')
    result = deepcopy(windows)
    result.update(schema_version='0.1.0-draft.2', artifact_kind='scored_candidates', stage='scored')
    result['windows_sha256'] = content_hash(windows)
    audio_metadata = (audio.metadata() if audio is not None else {
        'status': 'unavailable' if analysis['media']['has_audio'] else 'no_audio'})
    result['scoring'] = {
        'scorer': scorer.name, 'version': scorer.version, 'config': config.to_dict(),
        'config_sha256': content_hash(config.to_dict()), 'audio': audio_metadata,
        'warnings': ['volume_unavailable_zero_contribution'] if audio is None and analysis['media']['has_audio'] else [],
        'score_meaning': 'heuristic priority, not probability or measured quality',
    }
    for candidate in result['candidates']:
        candidate.update(scorer.score(candidate, analysis, audio))
        candidate.update(scorer=scorer.name, scoring_status='scored')
    result['candidates'].sort(key=lambda c: (-c['score'], *window_order(c)))
    for rank, candidate in enumerate(result['candidates'], 1):
        candidate['rank'] = rank
    validate_scored(result, analysis)
    return result


def validate_scored(data, analysis):
    _finite(data)
    schema = json.loads((ROOT / 'schemas/scored_candidates.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    error = next(Draft202012Validator(schema).iter_errors(data), None)
    if error:
        raise ValueError(f'{error.json_path}: {error.message}')
    try:
        config = RuleConfig(**data['scoring']['config'])
    except TypeError as error:
        raise ValueError('评分配置不合法') from error
    if data['scoring']['config_sha256'] != content_hash(config.to_dict()):
        raise ValueError('评分配置哈希不一致')
    pending = deepcopy(data)
    pending.update(schema_version='0.1.0-draft.1', artifact_kind='candidate_windows', stage='generated')
    pending.pop('scoring')
    pending.pop('windows_sha256')
    for candidate in pending['candidates']:
        candidate.pop('features')
        candidate.pop('rank')
        candidate.update(scoring_status='pending', scorer=None, score=None, reasons=[])
    pending['candidates'].sort(key=window_order)
    validate_candidates(pending, analysis)
    if content_hash(pending) != data['windows_sha256']:
        raise ValueError('评分改变了原候选或窗口来源哈希不一致')
    audio = data['scoring']['audio']
    if audio['status'] == 'measured':
        if (audio['normalized_sha256'] != analysis['media']['normalized_sha256']
                or audio['duration_seconds'] != analysis['media']['duration_seconds']
                or not analysis['media']['has_audio']):
            raise ValueError('音量来源不一致')
    elif audio['status'] != ('unavailable' if analysis['media']['has_audio'] else 'no_audio'):
        raise ValueError('音量缺失状态与媒体不一致')
    expected_warnings = ['volume_unavailable_zero_contribution'] if audio['status'] == 'unavailable' else []
    if data['scoring']['warnings'] != expected_warnings:
        raise ValueError('音量缺失警告不一致')
    ordered = sorted(data['candidates'], key=lambda c: (-c['score'], *window_order(c)))
    if ordered != data['candidates']:
        raise ValueError('候选未按分数与时间排序')
    weights = dict(zip(('keyword_density', 'pace_change', 'volume_peak', 'qa_pattern', 'duration_penalty'),
                       (config.keyword_weight, config.pace_weight, config.volume_weight,
                        config.qa_weight, -config.duration_penalty_weight)))
    for rank, candidate in enumerate(data['candidates'], 1):
        if candidate['rank'] != rank:
            raise ValueError('排名不连续')
        for name, weight in weights.items():
            feature = candidate['features'][name]
            if not math.isclose(feature['contribution'], feature['value'] * weight, abs_tol=2e-6, rel_tol=0):
                raise ValueError('分项分数与权重不一致')
        volume = candidate['features']['volume_peak']
        if volume['evidence'].get('status') != audio['status']:
            raise ValueError('音量状态不一致')
        if audio['status'] != 'measured' and (volume['value'] != 0 or volume['contribution'] != 0):
            raise ValueError('缺失的音量不能加分')
        expected = round(max(0, min(100, config.base_score + sum(
            f['contribution'] for f in candidate['features'].values()))), 6)
        if candidate['score'] != expected:
            raise ValueError('总分与分项不一致')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('analysis', type=Path)
    parser.add_argument('candidates', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--normalized-video', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    args = parser.parse_args()
    try:
        sources = [args.analysis, args.candidates, args.normalized_video, args.config]
        if args.output.exists() or any(args.output.resolve() == p.resolve() for p in sources if p):
            raise ValueError('输出已存在或指向输入，请使用新文件')
        analysis = json.loads(args.analysis.read_text(encoding='utf-8-sig'))
        windows = json.loads(args.candidates.read_text(encoding='utf-8-sig'))
        validate_candidates(windows, analysis)
        config = load_config(args.config)
        audio = measure_audio(args.normalized_video, analysis, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe) if args.normalized_video else None
        result = score_candidates(windows, analysis, config=config, audio=audio)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'FAIL: {error}\n')
    print(f"PASS: {result['candidate_count']} candidates scored with rule -> {args.output}")


if __name__ == '__main__':
    main()
