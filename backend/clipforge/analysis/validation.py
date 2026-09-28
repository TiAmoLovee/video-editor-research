"""开发期 analysis.json 校验；Schema 路径由调用方显式提供。"""

import argparse
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator


def _finite(value, path="$"):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path}: NaN/Infinity 不是有效 JSON 数值")
    if isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, f"{path}[{index}]")


def _partition(items, duration, name):
    """完整覆盖时间轴，允许浮点表示误差，不允许真实空隙或重叠。"""
    cursor = 0.0
    for item in items:
        if not math.isclose(item["start"], cursor, abs_tol=1e-6, rel_tol=0):
            raise ValueError(f"{name}: 时间轴存在空隙或重叠")
        cursor = item["end"]
    if not math.isclose(cursor, duration, abs_tol=1e-6, rel_tol=0):
        raise ValueError(f"{name}: 未覆盖完整时长")


def validate_analysis(data, schema):
    """校验字段结构及跨字段语义；失败抛出 ValueError。"""
    _finite(data)
    Draft202012Validator.check_schema(schema)
    error = next(Draft202012Validator(schema).iter_errors(data), None)
    if error is not None:
        raise ValueError(f"{error.json_path}: {error.message}")

    duration = data["media"]["duration_seconds"]
    for name in ("shots", "speech", "silence", "words", "sentences"):
        previous_start = -1
        previous_end = 0
        ids = set()
        for index, item in enumerate(data[name]):
            start, end = item["start"], item["end"]
            if not 0 <= start < end <= duration:
                raise ValueError(f"{name}[{index}]: 需要 0 <= start < end <= duration")
            if start < previous_start:
                raise ValueError(f"{name}[{index}]: 必须按 start 排序")
            if name in ("shots", "speech", "silence") and start < previous_end:
                raise ValueError(f"{name}[{index}]: 区间不能重叠")
            if "id" in item:
                if item["id"] in ids:
                    raise ValueError(f"{name}[{index}]: id 重复")
                ids.add(item["id"])
            previous_start, previous_end = start, end

    _partition(data["shots"], duration, "shots")
    if data["analyzers"]["shots"]["status"] != "ok":
        raise ValueError("shots: 视频镜头分析必须成功")
    if not data["media"]["has_audio"]:
        if any(data[name] for name in ("speech", "silence", "words", "sentences")):
            raise ValueError("无音轨时语音、静音、词和句子必须为空")
        if any(data["analyzers"][name]["status"] != "no_audio" for name in ("vad", "asr")):
            raise ValueError("无音轨时 VAD/ASR 状态必须为 no_audio")
    else:
        if any(data["analyzers"][name]["status"] != "ok" for name in ("vad", "asr")):
            raise ValueError("有音轨时 VAD/ASR 状态必须为 ok")
        regions = sorted(data["speech"] + data["silence"], key=lambda item: item["start"])
        _partition(regions, duration, "speech + silence")

    words = {item["id"]: item for item in data["words"]}
    used = []
    for sentence in data["sentences"]:
        references = sentence["word_ids"]
        if any(word_id not in words for word_id in references):
            raise ValueError(f"{sentence['id']}: 引用了不存在的词")
        members = [words[word_id] for word_id in references]
        if (sentence["start"] != min(item["start"] for item in members)
                or sentence["end"] != max(item["end"] for item in members)):
            raise ValueError(f"{sentence['id']}: 句子边界必须覆盖所引用词的起止时间")
        used.extend(references)
    if used != [item["id"] for item in data["words"]]:
        raise ValueError("句子必须按原词顺序完整分组，每个词恰好引用一次")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--schema", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        validate_analysis(data, schema)
    except (OSError, ValueError) as error:
        parser.exit(1, f"INVALID: {error}\n")
    print(f"VALID: {args.input} ({data['result_kind']}; schema {data['schema_version']})")


if __name__ == "__main__":
    main()
