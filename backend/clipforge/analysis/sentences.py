"""按词语、标点和停顿分组；保留原模型词条及其时间。"""

from functools import lru_cache
import re

END_MARK = re.compile(r'[。！？!?；;.]([”’"\'）)】\]]*)$')
SOFT_MARK = re.compile(r'[,，:：、]([”’"\'）)】\]]*)$')
CLOSERS = re.compile(r'^[”’"\'）)】\]]+$')
LATIN = re.compile(r"[A-Za-z0-9]+(?:[.'’_-][A-Za-z0-9]+)*")
SENTENCE_PARAMETERS = {
    "rule_version": "2", "max_characters": 50, "max_seconds": 15.0,
    "limits_are_soft": True, "gap_seconds": 1.0, "pause_seconds": 0.4,
    "min_pause_characters": 12, "preferred_pause_seconds": 0.15,
    "lookahead_pause_seconds": 0.1, "extra_characters": 15, "extra_seconds": 3.0,
    "tokenizer": "jieba", "tokenizer_version": "0.42.1", "hmm": False,
    "custom_dictionary": False,
}


@lru_cache(maxsize=1)
def _tokenizer():
    import jieba
    return jieba.Tokenizer()


def _units(words):
    """只允许同时位于词语边界和 ASR 词条边界处断句。"""
    text = "".join(w["text"] for w in words)
    boundaries = {end for _, _, end in _tokenizer().tokenize(text, HMM=False)}
    for match in LATIN.finditer(text):
        boundaries.difference_update(range(match.start() + 1, match.end()))
    units, pending, offset = [], [], 0
    for i, word in enumerate(words):
        pending.append(word)
        offset += len(word["text"])
        next_closer = i + 1 < len(words) and CLOSERS.fullmatch(words[i + 1]["text"].strip())
        if (offset in boundaries and not next_closer) or i == len(words) - 1:
            units.append({"words": pending, "text": "".join(w["text"] for w in pending),
                          "start": min(w["start"] for w in pending),
                          "end": max(w["end"] for w in pending)})
            pending = []
    return units


def _nearby_pause(group, units, index):
    length = sum(len(u["text"]) for u in group)
    end = max(u["end"] for u in group)
    for position in range(index, len(units)):
        following = units[position]
        if following["start"] - end >= SENTENCE_PARAMETERS["lookahead_pause_seconds"]:
            return True
        length += len(following["text"])
        end = max(end, following["end"])
        if (length > SENTENCE_PARAMETERS["max_characters"] + SENTENCE_PARAMETERS["extra_characters"]
                or end - group[0]["start"] > SENTENCE_PARAMETERS["max_seconds"] + SENTENCE_PARAMETERS["extra_seconds"]):
            return False
    return False


def split_sentences(words):
    if not words:
        return []
    result, group = [], []

    def flush(count=None):
        selected = group[:count] if count is not None else group[:]
        if not selected:
            return
        members = [word for unit in selected for word in unit["words"]]
        result.append({"id": f"s{len(result) + 1:06d}",
                       "start": min(w["start"] for w in members),
                       "end": max(w["end"] for w in members),
                       "text": "".join(w["text"] for w in members).strip(),
                       "word_ids": [w["id"] for w in members]})
        del group[:len(selected)]

    units = _units(words)
    for index, unit in enumerate(units):
        if group:
            length = sum(len(u["text"]) for u in group)
            gap = unit["start"] - max(u["end"] for u in group)
            if (gap >= SENTENCE_PARAMETERS["gap_seconds"]
                    or (gap >= SENTENCE_PARAMETERS["pause_seconds"]
                        and length >= SENTENCE_PARAMETERS["min_pause_characters"])):
                flush()
        while group and (sum(len(u["text"]) for u in group) + len(unit["text"]) > SENTENCE_PARAMETERS["max_characters"]
                         or max(unit["end"], *(u["end"] for u in group)) - group[0]["start"] > SENTENCE_PARAMETERS["max_seconds"]):
            if unit["start"] - max(u["end"] for u in group) >= SENTENCE_PARAMETERS["lookahead_pause_seconds"]:
                flush()
                break
            if _nearby_pause(group, units, index):
                break
            candidates, left_chars, left_end = [], 0, group[0]["end"]
            for i in range(1, len(group)):
                previous = group[i - 1]
                left_chars += len(previous["text"])
                left_end = max(left_end, previous["end"])
                pause = group[i]["start"] - left_end
                if left_chars >= SENTENCE_PARAMETERS["min_pause_characters"]:
                    if SOFT_MARK.search(previous["text"].rstrip()):
                        candidates.append((2, i))
                    elif pause >= SENTENCE_PARAMETERS["preferred_pause_seconds"]:
                        candidates.append((1, i))
            flush(max(candidates)[1] if candidates else len(group))
        group.append(unit)
        if END_MARK.search(unit["text"].rstrip()):
            flush()
    flush()
    return result
