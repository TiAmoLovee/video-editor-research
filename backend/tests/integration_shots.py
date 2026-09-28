"""隔离验证真实媒体流水线；API 上传/下载用 TestClient，队列投递使用替身。"""

import argparse
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from clipforge.api import app
from clipforge.analysis.validation import validate_analysis
from clipforge.config import media_tool
from clipforge.services.pipeline import process_video
from clipforge.storage.jobs import update_job


def exercise(source, data_dir):
    stages = []

    def record(*args, **kwargs):
        stages.append({"status": args[1], "stage": args[2], "progress": args[3]})
        return update_job(*args, **kwargs)

    with patch.dict(os.environ, {"CLIPFORGE_DATA_DIR": str(data_dir)}), TestClient(app) as client:
        with source.open("rb") as stream, patch("clipforge.routes.video.submit_video"):
            response = client.post("/tasks", files={"file": (source.name, stream, "video/mp4")})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        start = time.perf_counter()
        with patch("clipforge.services.pipeline.update_job", side_effect=record):
            process_video(task_id)
        elapsed = time.perf_counter() - start
        task = client.get(f"/tasks/{task_id}").json()
        assert task["status"] == "SUCCEEDED", task
        downloads = task["result"]["downloads"]
        direct = client.get(downloads["shots.json"])
        assert direct.status_code == 200
        shots = direct.json()
        vad_download = client.get(downloads["vad.json"])
        assert vad_download.status_code == 200
        vad = vad_download.json()
        asr_response = client.get(downloads["asr.json"])
        analysis_response = client.get(downloads["analysis.json"])
        assert asr_response.status_code == analysis_response.status_code == 200
        asr, analysis = asr_response.json(), analysis_response.json()
        schema = json.loads((Path(__file__).resolve().parents[2] / "schemas/analysis.schema.json").read_text(encoding="utf-8"))
        validate_analysis(analysis, schema)
        download = client.get(downloads["result.zip"])
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read("shots.json")) == shots
            assert json.loads(archive.read("vad.json")) == vad
            assert vad["media"]["normalized_sha256"] == shots["media"]["normalized_sha256"]
            assert vad["media"]["duration_seconds"] == shots["media"]["duration_seconds"]
            assert json.loads(archive.read("asr.json")) == asr
            assert json.loads(archive.read("analysis.json")) == analysis
            plan = json.loads(archive.read("clip_plan.json"))
            assert shots["media"]["total_frames"] == plan["total_frames"]
            assert set(archive.namelist()) == {
                "media_meta.json", "normalized_media_meta.json", "clip_plan.json", "shots.json", "vad.json", "asr.json", "analysis.json",
                *(clip["file"] for clip in plan["clips"])}
        assert [x["progress"] for x in stages] == sorted(x["progress"] for x in stages)
        assert "analyzing_shots" in [x["stage"] for x in stages]
        assert "analyzing_speech" in [x["stage"] for x in stages]
        assert client.get(f"/tasks/{task_id}").json() == task  # 刷新后的结果保持一致。
        return {"source_name": source.name, "task_id": task_id, "status": task["status"],
                "pipeline_seconds": round(elapsed, 4), "stages": stages,
                "clip_frames": [clip["frame_count"] for clip in plan["clips"]],
                "zip_crc_passed": True, "direct_and_zip_json_equal": True,
                "shot_result": shots, "vad_result": vad, "asr_result": asr, "analysis_result": analysis}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, help="可选：额外跑一条自备真实素材")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("记录已存在，请指定新输出文件")
    if args.video and not args.video.is_file():
        parser.error("自备视频不存在")
    ffmpeg = media_tool("ffmpeg")
    reports = []
    with tempfile.TemporaryDirectory(prefix="clipforge-shot-check-") as folder:
        base = Path(folder)
        fixture = base / "synthetic_31s.mp4"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                   "color=c=black:s=320x180:r=30:d=2", "-f", "lavfi", "-i",
                   "color=c=white:s=320x180:r=30:d=2", "-f", "lavfi", "-i",
                   "color=c=black:s=320x180:r=30:d=27", "-filter_complex",
                   "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264",
                   "-pix_fmt", "yuv420p", "-r", "30", "-an", str(fixture)]
        subprocess.run(command, capture_output=True, check=True, timeout=60)
        report = exercise(fixture, base / "synthetic-data")
        assert report["shot_result"]["cut_frames"] == [60, 120], report
        assert report["clip_frames"] == [900, 30], report
        report["reference_cut_frames"] = [60, 120]
        report["reference_kind"] = "synthetic_known_cuts_not_evaluation_dataset"
        reports.append(report)
        if args.video:
            report = exercise(args.video, base / "real-data")
            report["reference_kind"] = "real_video_smoke_without_manual_labels"
            reports.append(report)
    summary = {"scope": "native Windows pipeline and HTTP adapter; queue mocked; no Docker/Celery verification",
               "python": platform.python_version(), "platform": platform.system(), "results": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(reports)} real media executions; record: {args.output}")


if __name__ == "__main__":
    main()
