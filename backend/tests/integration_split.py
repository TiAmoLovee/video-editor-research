"""独立媒体集成实验：真实运行 FFmpeg；不属于 HTTP 任务接口测试。"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clipforge.media.split import split_video
from clipforge.config import media_tool


def main():
    parser = argparse.ArgumentParser(description="生成小分辨率素材并验证自动切片")
    parser.add_argument("--ffmpeg", default=media_tool("ffmpeg"))
    parser.add_argument("--ffprobe", default=media_tool("ffprobe"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="clipforge-integration-") as folder:
        root = Path(folder)
        for name, frames, audio, expected in [
            ("five_minutes", 9000, True, [900] * 10),
            ("one_frame_tail", 901, False, [900, 1]),
        ]:
            source = root / f"{name}.mp4"
            command = [args.ffmpeg, "-v", "error", "-nostdin", "-n", "-f", "lavfi",
                       "-i", "color=c=blue:s=160x90:r=30"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
            command += ["-t", f"{frames / 30:.9f}", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p"]
            command += ["-c:a", "aac"] if audio else ["-an"]
            command += [str(source)]
            subprocess.run(command, check=True, capture_output=True, timeout=120)
            output = root / f"{name}_clips"
            result = split_video(str(source), str(output), args.ffmpeg, args.ffprobe, 120)
            assert [clip["frame_count"] for clip in result["clips"]] == expected
            assert len(list(output.glob("*.mp4"))) == len(expected)
            assert json.loads((output / "clip_plan.json").read_text(encoding="utf-8")) == result
            print(f"PASS: {name}: {frames} frames -> {len(expected)} clips")

        # 编码器缺失时不能遗留一个看起来已完成的结果目录。
        failed = root / "failed_result"
        try:
            split_video(str(source), str(failed), str(root / "missing-ffmpeg.exe"), args.ffprobe, 120)
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing encoder was not rejected")
        assert not failed.exists()
        assert not list(root.glob(".clipforge-split-*"))
        print("PASS: failed encoding leaves no result directory or temporary directory")


if __name__ == "__main__":
    main()
