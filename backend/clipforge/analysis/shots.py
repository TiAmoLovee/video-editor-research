"""对归一化的 H.264 / CFR 30 fps 视频进行镜头检测，输出独立 shots.json。"""

import argparse
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import time

from clipforge.config import media_tool
from clipforge.media.probe import normalize_metadata, probe_video
from clipforge.media.split import read_frame_count


def frames_to_shots(ranges, total_frames):
    """用整数帧检查连续性，输出可直接用于 analysis.shots 的秒区间。"""
    if type(total_frames) is not int or total_frames <= 0:
        raise ValueError("没有有效视频帧")
    cursor = 0
    result = []
    for start, end in ranges:
        if (type(start) is not int or type(end) is not int
                or start != cursor or not start < end <= total_frames):
            raise ValueError("镜头帧边界存在空隙、重叠、空区间或越界")
        result.append({"start": start / 30, "end": end / 30})
        cursor = end
    if cursor != total_frames:
        raise ValueError("镜头结果未覆盖完整视频")
    return result


def _detect_frames(source, threshold, min_scene_len, downscale):
    # 延迟导入，使 API 启动与不涉及解码的测试不需要立即加载 OpenCV。
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector
    from scenedetect.detectors.content_detector import FlashFilter
    from scenedetect.scene_manager import Interpolation

    class CheckedContentDetector(ContentDetector):
        processed_frames = 0
        contiguous_frames = True

        def process_frame(self, frame_num, frame_img):
            self.contiguous_frames &= frame_num == self.processed_frames
            self.processed_frames += 1
            return super().process_frame(frame_num, frame_img)

    video = open_video(str(source), backend="opencv", max_decode_attempts=0)
    try:
        if not math.isclose(video.frame_rate, 30, rel_tol=0, abs_tol=1e-6):
            raise ValueError("解码器读取到的帧率不是 30 fps，请先归一化")
        manager = SceneManager()
        manager.auto_downscale = False
        manager.downscale = downscale
        manager.interpolation = Interpolation.LINEAR
        detector = CheckedContentDetector(
            threshold=threshold, min_scene_len=min_scene_len,
            weights=ContentDetector.Components(1.0, 1.0, 1.0, 0.0),
            luma_only=False, kernel_size=None, filter_mode=FlashFilter.Mode.MERGE,
        )
        manager.add_detector(detector)
        decoded = manager.detect_scenes(video=video, frame_skip=0, show_progress=False)
        if not detector.contiguous_frames or detector.processed_frames != decoded:
            raise ValueError("镜头检测未逐帧完整处理，可能存在损坏或尺寸异常的帧")
        scenes = manager.get_scene_list(start_in_scene=True)
        return [(start.get_frames(), end.get_frames()) for start, end in scenes], decoded
    finally:
        # OpenCV 后端公开 capture 属性；即使检测失败也释放文件句柄。
        video.capture.release()


def detect_shots(video_path, ffprobe=None, *, threshold=27.0, min_scene_len=15, downscale=1):
    """不加载整段视频；输出完整镜头分区，解码不完整时明确失败。"""
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or not 0 < threshold <= 255):
        raise ValueError("threshold 必须是 (0, 255] 内的有限数值")
    if type(min_scene_len) is not int or min_scene_len < 1:
        raise ValueError("min_scene_len 必须是正整数帧数")
    if type(downscale) is not int or downscale < 1:
        raise ValueError("downscale 必须是正整数")
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("镜头分析输入文件不存在")
    started = time.perf_counter()
    probe = media_tool("ffprobe", ffprobe)
    meta = normalize_metadata(probe_video(str(source), probe), str(source))
    if meta["video"]["codec"] != "h264" or meta["video"]["avg_frame_rate"] != "30/1":
        raise ValueError("请先归一化为 H.264 / CFR 30 fps 再进行镜头检测")
    expected = read_frame_count(source, meta["video"]["stream_index"], probe, 3600)
    ranges, decoded = _detect_frames(source, threshold, min_scene_len, downscale)
    if decoded != expected:
        raise ValueError(f"镜头解码帧数不完整：预期 {expected}，实际 {decoded}")
    shots = frames_to_shots(ranges, expected)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {
        "schema_version": "0.1.0", "artifact_kind": "shot_analysis", "result_kind": "measured",
        "media": {"normalized_sha256": digest.hexdigest(), "time_reference": "normalized_video",
                  "fps": 30, "total_frames": expected, "duration_seconds": expected / 30},
        "analyzer": {"status": "ok", "tool": "PySceneDetect.ContentDetector",
                     "version": version("scenedetect"), "model": None,
                     "parameters": {"threshold": threshold, "min_scene_len_frames": min_scene_len,
                                    "downscale": downscale, "auto_downscale": False, "frame_skip": 0,
                                    "weights": [1.0, 1.0, 1.0, 0.0], "luma_only": False,
                                    "kernel_size": None, "filter_mode": "MERGE", "interpolation": "LINEAR"}},
        "decoder": {"backend": "opencv", "version": version("opencv-python-headless"),
                    "max_decode_attempts": 0},
        "cut_frames": [start for start, _ in ranges[1:]],
        "shots": shots,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffprobe")
    parser.add_argument("--threshold", type=float, default=27.0)
    parser.add_argument("--min-scene-len", type=int, default=15, help="最小镜头间隔，单位为帧")
    parser.add_argument("--downscale", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.resolve() == args.video.resolve():
            raise ValueError("输出已存在或指向输入视频，请指定新文件")
        result = detect_shots(args.video, args.ffprobe, threshold=args.threshold,
                              min_scene_len=args.min_scene_len, downscale=args.downscale)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(f"PASS: {len(result['shots'])} shots, {result['media']['total_frames']} frames -> {args.output}")


if __name__ == "__main__":
    main()
