"""按模型词条边界分句；不拆词、不推造词级时间戳。"""

import re

END_MARK = re.compile(r'[。！？!?；;.]([”’"\'）)】\]]*)$')
SENTENCE_PARAMETERS = {"rule_version": "1", "max_characters": 50,
                       "max_seconds": 15.0, "gap_seconds": 1.0}


def split_sentences(words):
    result, group = [], []

    def flush():
        if not group:
            return
        result.append({"id": f"s{len(result) + 1:06d}",
                       "start": min(word["start"] for word in group),
                       "end": max(word["end"] for word in group),
                       "text": "".join(word["text"] for word in group).strip(),
                       "word_ids": [word["id"] for word in group]})
        group.clear()

    for word in words:
        if group and (word["start"] - group[-1]["end"] >= SENTENCE_PARAMETERS["gap_seconds"]
                      or word["end"] - group[0]["start"] > SENTENCE_PARAMETERS["max_seconds"]
                      or sum(len(item["text"]) for item in group) + len(word["text"]) > SENTENCE_PARAMETERS["max_characters"]):
            flush()
        group.append(word)
        if END_MARK.search(word["text"].rstrip()):
            flush()
    flush()
    return result
