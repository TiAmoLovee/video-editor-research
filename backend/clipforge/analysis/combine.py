"""汇总三路真实结果，发布前执行完整 Schema 与跨字段校验。"""

import hashlib
import json
from pathlib import Path

from clipforge.analysis.validation import validate_analysis


def combine_analysis(source_path, shots, vad, asr, schema_path=None):
    for item, kind in [(shots, "shot_analysis"), (vad, "vad_analysis"), (asr, "asr_analysis")]:
        if item["artifact_kind"] != kind or item["result_kind"] != "measured":
            raise ValueError("汇总只能使用对应组件的真实分析结果")
        for key in ("normalized_sha256", "time_reference", "duration_seconds", "total_frames", "fps"):
            if item["media"][key] != shots["media"][key]:
                raise ValueError(f"三路分析的媒体身份或时间轴不一致：{key}")
    if vad["media"]["has_audio"] != asr["media"]["has_audio"]:
        raise ValueError("VAD 与 ASR 的音轨状态不一致")
    with Path(source_path).open("rb") as file:
        source_hash = hashlib.file_digest(file, "sha256").hexdigest()
    result = {"schema_version": "0.1.0", "result_kind": "measured",
              "media": {"source_sha256": source_hash,
                        **{key: vad["media"][key] for key in ("normalized_sha256", "duration_seconds", "time_reference", "has_audio")}},
              "analyzers": {"shots": shots["analyzer"], "vad": vad["analyzer"], "asr": asr["analyzer"]},
              "shots": shots["shots"], "speech": vad["speech"], "silence": vad["silence"],
              "words": asr["words"], "sentences": asr["sentences"]}
    schema_file = Path(schema_path) if schema_path else Path(__file__).resolve().parents[3] / "schemas/analysis.schema.json"
    validate_analysis(result, json.loads(schema_file.read_text(encoding="utf-8")))
    return result
