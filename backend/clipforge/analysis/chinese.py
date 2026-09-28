"""按完整词条序列做繁简转换，保留词条 ID、时间及原始模型输出。"""

from functools import lru_cache


@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC
    return OpenCC("t2s")


def simplify_words(words):
    original = "".join(word["text"] for word in words)
    converted = _converter().convert(original)
    # 固定 t2s 字典保持字数。若未来更换词典改变字数，拒绝错误地映射时间。
    if len(converted) != len(original):
        raise ValueError("繁简转换改变了文本长度，无法保留词级时间映射")
    result, offset = [], 0
    for word in words:
        end = offset + len(word["text"])
        result.append({**word, "text": converted[offset:end]})
        offset = end
    return result
