"""从已校验的 analysis.json 生成完整句子窗口；本阶段不评分或渲染。"""

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
from itertools import islice
import json
import math
from pathlib import Path

from clipforge.analysis.validation import validate_analysis

ROOT = Path(__file__).resolve().parents[3]
GENERATOR_VERSION = "sentence-window-v1"


@dataclass(frozen=True)
class WindowOptions:
    min_seconds: float = 15.0
    max_seconds: float = 90.0
    max_gap_seconds: float = 3.0
    max_candidates: int = 10000

    def __post_init__(self):
        values = (self.min_seconds, self.max_seconds, self.max_gap_seconds)
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) for v in values):
            raise ValueError("窗口参数必须为有限数值")
        if not 15 <= self.min_seconds <= self.max_seconds <= 90:
            raise ValueError("窗口需要满足 15 <= min_seconds <= max_seconds <= 90")
        if self.max_gap_seconds < 0:
            raise ValueError("max_gap_seconds 不能为负数")
        if type(self.max_candidates) is not int or self.max_candidates < 1:
            raise ValueError("max_candidates 必须为正整数")


def content_hash(data):
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _units(sentences):
    # 原契约允许句子重叠。将重叠句组成不可拆分的单元，避免截断相邻句。
    units = []
    for sentence in sentences:
        if units and sentence["start"] < units[-1]["end"]:
            units[-1]["sentences"].append(sentence)
            units[-1]["end"] = max(units[-1]["end"], sentence["end"])
        else:
            units.append({"start": sentence["start"], "end": sentence["end"],
                          "sentences": [sentence]})
    return units


def generate_candidates(analysis, options=None, *, analysis_schema=None):
    options = options or WindowOptions()
    schema = analysis_schema or json.loads(
        (ROOT / "schemas/analysis.schema.json").read_text(encoding="utf-8"))
    validate_analysis(analysis, schema)
    identity = content_hash(analysis)
    units = _units(analysis["sentences"])
    candidates = []
    for index, first in enumerate(units):
        members = []
        previous_end = first["start"]
        for unit in islice(units, index, None):
            if members and unit["start"] - previous_end > options.max_gap_seconds:
                break
            duration = round(unit["end"] - first["start"], 9)
            if duration > options.max_seconds:
                break
            members.extend(unit["sentences"])
            previous_end = unit["end"]
            if duration < options.min_seconds:
                continue
            if len(candidates) >= options.max_candidates:
                raise ValueError("候选数量超过 max_candidates；请缩小窗口或提高明确的上限")
            references = [s["id"] for s in members]
            key = {"analysis": identity, "version": GENERATOR_VERSION,
                   "source_sentences": references}
            candidates.append({
                "id": "c_" + content_hash(key), "start": first["start"],
                "end": unit["end"], "duration_seconds": round(duration, 9),
                "text": "\n".join(s["text"] for s in members),
                "source_sentences": references,
                "scoring_status": "pending", "score": None, "reasons": [], "scorer": None,
            })
    warnings = []
    if not units:
        warnings.append("no_sentences")
    if any(u["end"] - u["start"] > options.max_seconds for u in units):
        warnings.append("overlong_sentence_unit_skipped")
    if not candidates:
        warnings.append("no_eligible_window")
    return {
        "schema_version": "0.1.0-draft.1", "artifact_kind": "candidate_windows",
        "stage": "generated", "input_result_kind": analysis["result_kind"],
        "analysis_sha256": identity, "media": deepcopy(analysis["media"]),
        "generator": {"version": GENERATOR_VERSION, "options": asdict(options)},
        "candidate_count": len(candidates), "candidates": candidates, "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-seconds", type=float, default=15)
    parser.add_argument("--max-seconds", type=float, default=90)
    parser.add_argument("--max-gap-seconds", type=float, default=3)
    parser.add_argument("--max-candidates", type=int, default=10000)
    args = parser.parse_args()
    try:
        if args.analysis.resolve() == args.output.resolve():
            raise ValueError("输出不能覆盖输入 analysis.json")
        analysis = json.loads(args.analysis.read_text(encoding="utf-8-sig"))
        options = WindowOptions(args.min_seconds, args.max_seconds,
                                args.max_gap_seconds, args.max_candidates)
        result = generate_candidates(analysis, options)
        from clipforge.decision.validation import validate_candidates
        validate_candidates(result, analysis)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # 拒绝覆盖历史产物；调用方用新路径生成新的结果。
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(f"PASS: {result['candidate_count']} unscored candidates -> {args.output}")


if __name__ == "__main__":
    main()
