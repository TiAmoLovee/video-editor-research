"""每次上传单独保存镜头设置，避免把一段录屏的坐标应用到其他素材。"""
import json
import math


def validate_options(value):
    if not isinstance(value, dict) or set(value) - {"method", "region"}:
        raise ValueError("镜头设置格式无效")
    if value.get("method") != "robust":
        raise ValueError("不支持的镜头检测选项")
    region = value.get("region")
    if region is not None:
        if (not isinstance(region, list) or len(region) != 4
                or any(isinstance(x, bool) or not isinstance(x, (float, int))
                       or not math.isfinite(x) for x in region)):
            raise ValueError("视频区域必须是4个有限比例值")
        left, top, right, bottom = region
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("视频区域超出画面或为空")
        if right - left < 0.01 or bottom - top < 0.01:
            raise ValueError("框选区域太小，请重新选择")
    return {"method": "robust", "region": region}


def parse_options(encoded):
    if encoded is None:
        return None
    if not isinstance(encoded, str) or len(encoded) > 2048:
        raise ValueError("镜头设置过长或格式无效")
    try:
        value = json.loads(encoded)
    except (ValueError, TypeError) as error:
        raise ValueError("镜头设置不是有效JSON") from error
    return validate_options(value)


def detector_options(value, metadata):
    value = validate_options(value)
    options = {"method": "robust", "min_scene_len": 6}
    region = value["region"]
    if region is not None:
        width, height = metadata["video"]["width"], metadata["video"]["height"]
        if type(width) is not int or type(height) is not int or min(width, height) <= 0:
            raise ValueError("归一化视频尺寸无效")
        left, top, right, bottom = region
        options["crop"] = (math.floor(left*width), math.floor(top*height),
                           min(width, math.ceil(right*width)), min(height, math.ceil(bottom*height)))
    return options
