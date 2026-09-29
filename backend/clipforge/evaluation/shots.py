"""准备镜头人工标注，并按一对一匹配计算镜头切点 F1。"""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from clipforge.analysis.shots import detect_shots, frames_to_shots
from clipforge.media.normalize import normalize_video

CATEGORIES = ("interview", "course", "vlog")
DEFAULT_TOLERANCE = 3  # 本项目约定：30 fps 下 ±0.1 秒，不是任务书给出的阈值。


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_new(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("SHA256 必须为 64 位小写十六进制字符串")
    return value


def cut_list(values, total_frames):
    if type(total_frames) is not int or total_frames <= 0:
        raise ValueError("total_frames 必须是正整数")
    if (not isinstance(values, list)
            or any(type(x) is not int or not 0 < x < total_frames for x in values)
            or values != sorted(set(values))):
        raise ValueError("切点必须是严格递增的整数帧；不包含片头 0 或片尾 total_frames")
    return values


def prediction_media(prediction):
    if (prediction.get("artifact_kind") != "shot_analysis"
            or prediction.get("result_kind") != "measured"
            or prediction.get("schema_version") != "0.1.0"
            or prediction["analyzer"]["status"] != "ok"):
        raise ValueError("需要实际镜头模块产出的 shots.json")
    media = prediction["media"]
    digest(media["normalized_sha256"])
    if media["time_reference"] != "normalized_video" or media["fps"] != 30:
        raise ValueError("评测统一使用归一化视频的 30 fps 时间轴")
    n = media["total_frames"]
    cuts = cut_list(prediction["cut_frames"], n)
    duration = media["duration_seconds"]
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or abs(duration - n / 30) > 1e-6):
        raise ValueError("时长与帧数不一致")
    boundaries = [0, *cuts, n]
    expected = frames_to_shots(list(zip(boundaries[:-1], boundaries[1:])), n)
    if prediction["shots"] != expected:
        raise ValueError("shots 分区与 cut_frames 不一致")
    return media


def metrics(tp, fp, fn):
    denominator = 2 * tp + fp + fn
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / denominator if denominator else None}


def match_cuts(reference, predicted, total_frames, tolerance=DEFAULT_TOLERANCE):
    """有序切点最早可行一对一匹配，最大化 TP，不重复消费邻近切点。"""
    if type(tolerance) is not int or tolerance < 0:
        raise ValueError("容差必须是非负整数帧")
    cut_list(reference, total_frames)
    cut_list(predicted, total_frames)
    i = j = 0
    pairs, missed, extra = [], [], []
    while i < len(reference) and j < len(predicted):
        r, p = reference[i], predicted[j]
        if p < r - tolerance:
            extra.append(p)
            j += 1
        elif r < p - tolerance:
            missed.append(r)
            i += 1
        else:
            pairs.append({"reference_frame": r, "predicted_frame": p, "offset_frames": p - r})
            i += 1
            j += 1
    missed.extend(reference[i:])
    extra.extend(predicted[j:])
    return {**metrics(len(pairs), len(extra), len(missed)),
            "tolerance_frames": tolerance, "matches": pairs,
            "missed_frames": missed, "extra_frames": extra,
            "correct_no_cuts": not reference and not predicted}


def evaluate(annotation, prediction, tolerance=DEFAULT_TOLERANCE):
    if (annotation.get("format_version") != "1"
            or annotation.get("annotation_kind") != "human_shot_boundaries"
            or annotation.get("status") != "verified"
            or not isinstance(annotation.get("reviewer"), str) or not annotation["reviewer"].strip()
            or not isinstance(annotation.get("reviewed_at"), str) or not annotation["reviewed_at"].strip()):
        raise ValueError("只计分已人工复核、填写复核人和日期的标注；草稿不能作为真实标签")
    media = prediction_media(prediction)
    digest(annotation["source_sha256"])
    if any(annotation["media"][k] != media[k] for k in
           ("normalized_sha256", "time_reference", "fps", "total_frames", "duration_seconds")):
        raise ValueError("人工标注与预测的媒体身份/时间轴不匹配")
    reference = annotation["cut_frames"]
    return {"media": media, "source_sha256": annotation["source_sha256"],
            "prediction_analyzer": prediction["analyzer"],
            "primary": match_cuts(reference, prediction["cut_frames"], media["total_frames"], tolerance),
            "exact": match_cuts(reference, prediction["cut_frames"], media["total_frames"], 0)}


def prepare(source, output_dir, ffmpeg=None, ffprobe=None):
    source = Path(source).resolve()
    # 先确认输入，再创建独占目录；不会覆盖人工标注或原视频。
    source_hash = sha256(source)
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=False)
    normalized = folder / "normalized.mp4"
    normalize_video(str(source), str(normalized), ffmpeg, ffprobe)
    prediction = detect_shots(normalized, ffprobe)
    if sha256(source) != source_hash:
        raise ValueError("准备期间原视频发生变化，请用新目录重新准备")
    # 标注初值必须为 null。[] 只表示人已确认整片没有切点。
    annotation = {"format_version": "1", "annotation_kind": "human_shot_boundaries",
                  "source_name": source.name, "source_sha256": source_hash,
                  "media": prediction["media"], "status": "draft",
                  "reviewer": "", "reviewed_at": "", "cut_frames": None,
                  "notes": "请逐帧观看 normalized.mp4；标记新镜头的第一帧，帧号从 0 开始。"}
    write_new(folder / "annotation.json", annotation)
    write_new(folder / "prediction.json", prediction)
    return {"output_dir": str(folder.resolve()), "total_frames": prediction["media"]["total_frames"],
            "annotation_status": "draft", "human_f1": None}


def _aggregate(rows):
    return metrics(*(sum(row["score"]["primary"][k] for row in rows) for k in ("tp", "fp", "fn")))


def score_dataset(manifest_path):
    path = Path(manifest_path).resolve()
    manifest = read_json(path)
    if manifest.get("format_version") != "1" or not isinstance(manifest.get("videos"), list):
        raise ValueError("评测清单格式错误")
    tolerance = manifest["tolerance_frames"]
    match_cuts([], [], 1, tolerance)
    seen_ids, seen_sources, seen_normalized = set(), set(), set()
    rows, pending = [], []
    detector = None
    for item in manifest["videos"]:
        identity = item["id"]
        if not isinstance(identity, str) or not identity.strip() or identity in seen_ids:
            raise ValueError("素材 id 为空或重复")
        source_hash = digest(item["source_sha256"])
        if source_hash in seen_sources:
            raise ValueError("同一原始视频被重复登记，不能增加评测数量")
        seen_ids.add(identity)
        seen_sources.add(source_hash)
        if item.get("kind") != "real" or item.get("split") not in ("development", "evaluation"):
            raise ValueError("清单只接受真实视频，并须区分 development/evaluation")
        category = item.get("category")
        if category not in (*CATEGORIES, "unknown", "other"):
            raise ValueError("未知分类值")
        if category not in CATEGORIES:
            pending.append({"id": identity, "reason": "category_not_eligible"})
            continue
        annotation_path = path.parent / item["annotation"]
        prediction_path = path.parent / item["prediction"]
        if not annotation_path.is_file() or not prediction_path.is_file():
            pending.append({"id": identity, "reason": "missing_annotation_or_prediction"})
            continue
        annotation, prediction = read_json(annotation_path), read_json(prediction_path)
        if annotation.get("status") == "draft":
            pending.append({"id": identity, "reason": "annotation_draft"})
            continue
        score = evaluate(annotation, prediction, tolerance)
        if detector is not None and detector != score["prediction_analyzer"]:
            raise ValueError("同一轮评测须使用一致的镜头检测器版本和参数")
        detector = score["prediction_analyzer"]
        if score["source_sha256"] != source_hash:
            raise ValueError("人工标注的原片身份与清单不一致")
        normalized_hash = score["media"]["normalized_sha256"]
        if normalized_hash in seen_normalized:
            raise ValueError("同一归一化视频重复计分")
        seen_normalized.add(normalized_hash)
        rows.append({"id": identity, "category": category, "split": item["split"], "score": score,
                     "annotation_sha256": sha256(annotation_path), "prediction_sha256": sha256(prediction_path)})
    counts = Counter(row["category"] for row in rows)
    ready = len(rows) >= 10 and all(counts[c] >= 3 for c in CATEGORIES) and not pending
    overall = _aggregate(rows)
    return {"format_version": "1", "metric": "micro F1, one-to-one internal cut matching",
            "manifest_sha256": sha256(path), "tolerance_frames": tolerance,
            "tolerance_seconds": tolerance / 30, "registered_videos": len(manifest["videos"]),
            "scored_videos": len(rows), "pending": pending,
            "category_counts": {c: counts[c] for c in CATEGORIES}, "dataset_ready": ready,
            "overall": overall, "by_category": {c: _aggregate([r for r in rows if r["category"] == c]) for c in CATEGORIES},
            "by_split": {s: _aggregate([r for r in rows if r["split"] == s]) for s in ("development", "evaluation")},
            "shot_f1_target_met": ready and overall["f1"] is not None and overall["f1"] >= 0.75,
            "week3_complete": False, "videos": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="归一化并检测；生成待人工填写的空白标注")
    prep.add_argument("--source", type=Path, required=True)
    prep.add_argument("--output-dir", type=Path, required=True)
    prep.add_argument("--ffmpeg")
    prep.add_argument("--ffprobe")
    score = sub.add_parser("score", help="对一条已人工复核的素材计分")
    score.add_argument("--annotation", type=Path, required=True)
    score.add_argument("--prediction", type=Path, required=True)
    score.add_argument("--tolerance-frames", type=int, default=DEFAULT_TOLERANCE)
    score.add_argument("--output", type=Path, required=True)
    dataset = sub.add_parser("dataset", help="汇总真实素材，报告缺项和分类覆盖")
    dataset.add_argument("--manifest", type=Path, required=True)
    dataset.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.source, args.output_dir, args.ffmpeg, args.ffprobe)
        else:
            if args.output.exists():
                raise ValueError("输出已存在，请另选路径，保留历史评测记录")
            result = (evaluate(read_json(args.annotation), read_json(args.prediction), args.tolerance_frames)
                      if args.command == "score" else score_dataset(args.manifest))
            write_new(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.exit(1, f"FAIL: {exc}\n")


if __name__ == "__main__":
    main()
