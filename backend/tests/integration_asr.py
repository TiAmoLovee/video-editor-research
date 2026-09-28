"""真实模型与完整分析流水线验证；队列替身，不代替 Docker/Celery 验收。"""

import argparse
import json
from pathlib import Path
import platform
import tempfile

from clipforge.analysis.model import model_directory, verify_model
from clipforge.config import media_tool
from integration_shots import exercise
from integration_vad import create_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, action="append", default=[], help="可重复指定真实或合成语音视频")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or any(not video.is_file() for video in args.video):
        parser.error("输出须为新文件，视频须存在")
    identity = verify_model(model_directory())
    reports = []
    with tempfile.TemporaryDirectory(prefix="clipforge-asr-check-") as directory:
        base = Path(directory)
        for kind in ("no_audio", "silence"):
            source = base / f"{kind}.mp4"
            create_fixture(media_tool("ffmpeg"), source, kind)
            report = exercise(source, base / f"{kind}-data")
            result = report["analysis_result"]
            assert result["words"] == result["sentences"] == []
            assert result["analyzers"]["asr"]["status"] == ("no_audio" if kind == "no_audio" else "ok")
            report["reference_kind"] = "synthetic_absent_or_silent_audio"
            reports.append(report)
        for index, video in enumerate(args.video):
            report = exercise(video, base / f"video-{index}-data")
            # 该入口用于带语音素材的冒烟验证；没有转写内容应人工检查，不作成功证据。
            assert report["analysis_result"]["words"], "语音素材未产出任何词条，请检查素材与模型结果"
            report["reference_kind"] = "user_selected_video_without_manual_word_alignment"
            reports.append(report)
    summary = {"scope": "native CPU int8 inference and HTTP adapters; queue mocked; no Docker/Celery verification",
               "python": platform.python_version(), "model": identity, "results": reports,
               "limitations": ["No manual transcription accuracy or <=0.3s alignment acceptance.",
                               "No analysis-result cache or cache speedup measurement."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(reports)} real media/model executions -> {args.output}")


if __name__ == "__main__":
    main()
