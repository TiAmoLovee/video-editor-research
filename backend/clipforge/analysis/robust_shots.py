"""实验性颜色突变检测；有ROI时使用画外变化过滤录屏窗口干扰。"""
import math

PARAMETERS = {
    "feature_width": 384, "max_feature_height": 1024, "feature_interpolation": "AREA",
    "min_rgb_change": 12.0, "relative_change": 3.0, "window_width_frames": 2,
    "outside_mean_threshold": 8.0, "outside_fraction_threshold": 0.08,
    "outside_pixel_threshold": 20.0, "outside_preview_size": [384, 216],
    "outside_margin_pixels": 2,
    "selection": "strongest_first_minimum_spacing",
}


def select_cuts(features, min_scene_len):
    """输入仅为逐帧测量值，不接收人工参考。每行：RGB均差、画外均差、画外变化比例。"""
    if type(min_scene_len) is not int or min_scene_len < 1:
        raise ValueError("min_scene_len 必须是正整数帧数")
    if any(len(row) != 3 or any(not math.isfinite(x) or x < 0 for x in row) for row in features):
        raise ValueError("无效的逐帧变化测量值")
    candidates, suppressed = [], []
    count = len(features)
    for frame in range(min_scene_len, count):
        value, outside, fraction = features[frame]
        if value < PARAMETERS["min_rgb_change"]:
            continue
        lo = max(1, frame - PARAMETERS["window_width_frames"])
        hi = min(count, frame + PARAMETERS["window_width_frames"] + 1)
        neighbors = [features[n][0] for n in range(lo, hi) if n != frame]
        # 边缘使用可获得的邻居；没有邻居时只作绝对变化判断。
        average = sum(neighbors) / len(neighbors) if neighbors else 0.0
        if value < PARAMETERS["relative_change"] * max(average, 0.01):
            continue
        if (outside >= PARAMETERS["outside_mean_threshold"]
                and fraction >= PARAMETERS["outside_fraction_threshold"]):
            suppressed.append(frame)
        else:
            candidates.append(frame)
    cuts = []
    # 仅保存特征；不保存整段视频画面。
    from bisect import bisect_left
    for frame in sorted(candidates, key=lambda n: (-features[n][0], n)):
        position = bisect_left(cuts, frame)
        if ((position == 0 or frame - cuts[position-1] >= min_scene_len)
                and (position == len(cuts) or cuts[position] - frame >= min_scene_len)):
            cuts.insert(position, frame)
    return cuts, suppressed


def measure_frames(source, crop=None):
    """顺序解码、校验尺寸；ROI仅影响测量，不改变时间轴。"""
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(source))
    try:
        if not capture.isOpened():
            raise ValueError("无法打开镜头分析视频")
        if not math.isclose(capture.get(cv2.CAP_PROP_FPS), 30, rel_tol=0, abs_tol=1e-6):
            raise ValueError("解码器读取到的帧率不是 30 fps，请先归一化")
        shape, previous, previous_outer, mask = None, None, None, None
        features = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if shape is None:
                shape = frame.shape
                height, width = shape[:2]
                if crop is not None:
                    x0, y0, x1, y1 = crop
                    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                        raise ValueError("crop 超出归一化视频尺寸")
                    sx, sy = 384 / width, 216 / height
                    mask = np.ones((216, 384), bool)
                    mask[max(0, int(y0*sy)-2):min(216, int(y1*sy)+3),
                         max(0, int(x0*sx)-2):min(384, int(x1*sx)+3)] = False
                    if not mask.any():
                        mask = None
            if frame.shape != shape:
                raise ValueError("镜头检测未逐帧完整处理：帧尺寸发生变化")
            if not math.isclose(capture.get(cv2.CAP_PROP_POS_FRAMES), len(features)+1, abs_tol=1e-6):
                raise ValueError("镜头检测未逐帧完整处理：解码帧位置不连续")
            region = frame if crop is None else frame[crop[1]:crop[3], crop[0]:crop[2]]
            feature_width = PARAMETERS["feature_width"]
            scale = min(feature_width / region.shape[1], PARAMETERS["max_feature_height"] / region.shape[0])
            size = (max(1, round(region.shape[1]*scale)), max(1, round(region.shape[0]*scale)))
            target = cv2.resize(region, size,
                                interpolation=cv2.INTER_AREA)
            outer = cv2.resize(frame, (384, 216), interpolation=cv2.INTER_AREA) if mask is not None else None
            row = [0.0, 0.0, 0.0]
            if previous is not None:
                row[0] = float(cv2.absdiff(target, previous).mean())
                if mask is not None:
                    difference = cv2.absdiff(outer, previous_outer).mean(axis=2)[mask]
                    row[1] = float(difference.mean())
                    row[2] = float((difference >= PARAMETERS["outside_pixel_threshold"]).mean())
            features.append(row)
            previous, previous_outer = target, outer
        if not features:
            raise ValueError("没有有效视频帧")
        return features
    finally:
        capture.release()


def detect_robust_frames(source, min_scene_len, crop=None):
    features = measure_frames(source, crop)
    cuts, suppressed = select_cuts(features, min_scene_len)
    boundaries = [0, *cuts, len(features)]
    return list(zip(boundaries[:-1], boundaries[1:])), len(features), suppressed
