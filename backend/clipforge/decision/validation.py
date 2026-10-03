"""候选窗口草案：结构、来源、完整句子及时间约束校验。"""

import json
import math

from jsonschema import Draft202012Validator

from clipforge.analysis.validation import _finite, validate_analysis
from clipforge.decision.candidates import ROOT, WindowOptions, content_hash


def validate_candidates(data, analysis, schema=None):
    validate_analysis(analysis, json.loads(
        (ROOT / "schemas/analysis.schema.json").read_text(encoding="utf-8")))
    _finite(data)
    schema = schema or json.loads(
        (ROOT / "schemas/candidates.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    error = next(Draft202012Validator(schema).iter_errors(data), None)
    if error:
        raise ValueError(f"{error.json_path}: {error.message}")
    options = WindowOptions(**data["generator"]["options"])
    if (data["analysis_sha256"] != content_hash(analysis)
            or data["media"] != analysis["media"]
            or data["input_result_kind"] != analysis["result_kind"]):
        raise ValueError("候选与来源分析不匹配")
    if data["candidate_count"] != len(data["candidates"]):
        raise ValueError("候选计数不匹配")
    if data["candidate_count"] > options.max_candidates:
        raise ValueError("候选数量超过配置上限")
    sentences = analysis["sentences"]
    indices = {s["id"]: i for i, s in enumerate(sentences)}
    seen, windows = set(), set()
    for candidate in data["candidates"]:
        refs = candidate["source_sentences"]
        if any(ref not in indices for ref in refs):
            raise ValueError("引用了不存在的句子")
        positions = [indices[ref] for ref in refs]
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise ValueError("候选必须引用连续且有序的句子")
        members = sentences[positions[0]:positions[-1] + 1]
        start, end = candidate["start"], candidate["end"]
        if start != min(s["start"] for s in members) or end != max(s["end"] for s in members):
            raise ValueError("候选边界必须覆盖完整句子")
        if not options.min_seconds <= round(end - start, 9) <= options.max_seconds:
            raise ValueError("候选时长不在配置范围内")
        if not math.isclose(candidate["duration_seconds"], end - start, abs_tol=1e-8, rel_tol=0):
            raise ValueError("候选时长与起止时间不一致")
        # 任一未引用句子与窗口有实质交集，都会造成半句或缺失引用。
        for s in sentences:
            if s["start"] < end and s["end"] > start and s["id"] not in refs:
                raise ValueError("候选截断了未引用的相邻句子")
        cursor = members[0]["end"]
        for s in members[1:]:
            if s["start"] - cursor > options.max_gap_seconds:
                raise ValueError("候选跨过了过长停顿")
            cursor = max(cursor, s["end"])
        if candidate["text"] != "\n".join(s["text"] for s in members):
            raise ValueError("候选文本与来源句子不一致")
        if candidate["id"] in seen or (start, end) in windows:
            raise ValueError("重复候选")
        seen.add(candidate["id"])
        windows.add((start, end))
