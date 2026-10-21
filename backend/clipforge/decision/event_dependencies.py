"""Auditable local discourse dependencies before lexical topic segmentation.

Links say that a cut would separate an explicit surface construction. They do
not certify that the next unit answers a question, that a quoted event happened,
or that the resulting clip tells a complete event. In particular, punctuation
and lexical cohesion alone cannot provide semantic event-completeness labels.

This module uses no ASR edits, model calls, footage-specific entities or timing
thresholds. The caller decides whether a link crosses a hard media pause and
must report any dependency it cannot retain. All positions are zero-based.
"""

import math
import re
import unicodedata


VERSION = "surface-event-dependencies-v1"
MAX_UNITS = 10000
MAX_SENTENCES = 10000
MAX_TEXT_CHARACTERS = 1000000
MAX_QUOTE_DEPTH = 64
MAX_DEPENDENCIES = 20000

_CLOSERS = "”’\"'）)】]」』"
_TERMINAL = re.compile(r"[。！？!?；;.]$")
_QUESTION = re.compile(r"[?？]$")
_INTRODUCTION = re.compile(r"[:：]$")
# Restrict punctuation-free recognition to full question-shaped turns. A word
# such as 'why' or '为什么' occurring inside an assertion is not sufficient.
_QUESTION_PREFIX = re.compile(
    r"^(?:(?:请问|那么|那)[,，\s]*)?(?:为什么|为何|怎么会|如何)(?=\S).+")
_ADDRESSED_QUESTION = re.compile(
    r"(?:你|您)(?:觉得|认为|看)[^。！？!?；;\n]{0,80}"
    r"(?:是什么(?:样的)?原因|是因为什么|为什么|怎么办|怎么回事)$")
_QUESTION_TAIL = re.compile(
    r"(?:为什么呢|怎么办呢|怎么回事|你怎么看|您怎么看|你觉得呢|您觉得呢|是否如此)$")
_INLINE_RESPONSE = re.compile(r"(?:因为|答案是|原因是|所以答案|because\b|the answer is\b)", re.I)
_ENGLISH_QUESTION = re.compile(
    r"^(?:why|how|what|when|where|who)\s+(?:is|are|was|were|do|does|did|can|could|would|will|should)\b",
    re.I)
_DEMONSTRATIVE = re.compile(r"(?:这个|那个)$")
# A small grammatical head vocabulary, rather than a transcript-specific
# phrase. This intentionally misses unknown heads instead of joining every
# standalone demonstrative answer (e.g. '我就选这个') to unrelated speech.
_NOMINAL_HEAD = re.compile(
    r"^(?:时候|原因|问题|事情|过程|地方|方向|办法|方式|结果|计划|东西|人|目标|阶段|状态|情况|机会|意义|故事)")

_OPEN_QUOTES = {"“": "”", "‘": "’", "「": "」", "『": "』"}
_CLOSE_QUOTES = {value: key for key, value in _OPEN_QUOTES.items()}


def _validated_units(units):
    if not isinstance(units, list):
        raise ValueError("event dependency units must be a list")
    if len(units) > MAX_UNITS:
        raise ValueError("event dependency unit limit exceeded; input was not truncated")
    text_count = sentence_count = 0
    previous_end = 0.0
    result = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError("event dependency unit must be an object")
        start, end = unit.get("start"), unit.get("end")
        if (any(isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) for value in (start, end))
                or start < 0 or end <= start or (index and start < previous_end)):
            raise ValueError("event dependency units must have ordered finite non-overlapping times")
        previous_end = end
        sentences = unit.get("sentences")
        if not isinstance(sentences, list) or not sentences:
            raise ValueError("event dependency unit needs source sentences")
        sentence_count += len(sentences)
        if sentence_count > MAX_SENTENCES:
            raise ValueError("event dependency sentence limit exceeded; input was not truncated")
        for sentence in sentences:
            if (not isinstance(sentence, dict)
                    or not isinstance(sentence.get("id"), str) or not sentence["id"]
                    or not isinstance(sentence.get("text"), str)):
                raise ValueError("event dependency sentence needs an id and text")
            text_count += len(sentence["text"])
            if text_count > MAX_TEXT_CHARACTERS:
                raise ValueError("event dependency text limit exceeded; input was not truncated")
        text = "\n".join(sentence["text"] for sentence in sentences)
        # Normalization is used only for matching; saved source text is verbatim.
        normalized = unicodedata.normalize("NFKC", text).strip()
        result.append({"text": text, "normalized": normalized,
                       "tail": normalized.rstrip(_CLOSERS).rstrip(),
                       "source_sentences": [sentence["id"] for sentence in sentences]})
    return result


def _question_kind(text):
    if _QUESTION.search(text):
        return "question_mark"
    if not text or _TERMINAL.search(text):
        return None
    # A completed answer within the same indivisible unit must not invent a
    # dependency on a new topic merely because that unit also contains a question.
    tail = re.split(r"[。！？!?；;\n]", text)[-1].strip()
    if not tail or _INLINE_RESPONSE.search(tail):
        return None
    if (_QUESTION_PREFIX.search(tail) or _ADDRESSED_QUESTION.search(tail)
            or _QUESTION_TAIL.search(tail) or _ENGLISH_QUESTION.search(tail)):
        return "unpunctuated_question_shape"
    return None


def _apostrophe(text, index, *, matching_quote_open=False):
    # Apostrophes inside Latin words and possessives are not quotation marks.
    # Chinese single quotation marks remain directional quote punctuation.
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    latin_previous = previous.isascii() and previous.isalnum()
    latin_following = following.isascii() and following.isalnum()
    return latin_previous and (latin_following or (
        not matching_quote_open and (not following or following.isspace())))


def build_event_dependencies(units):
    """Return surface dependency links and unresolved source-edge evidence.

    ``links`` use inclusive first/last unit indices and remain independent of
    timing policy. ``open_edges`` use ``side='right'`` for missing continuation
    and ``side='left'`` for a quotation closer without its opener. A tail marker
    can be ambiguous (for example, a standalone demonstrative answer); the
    evidence says so explicitly. No positive semantic-quality verdict is emitted.

    Work and evidence size are linear in source text plus emitted dependencies.
    Fixed ceilings fail explicitly instead of returning partial audit evidence.
    """
    texts = _validated_units(units)
    links, open_edges = [], []

    def add(collection, value):
        if len(links) + len(open_edges) >= MAX_DEPENDENCIES:
            raise ValueError("event dependency evidence limit exceeded; no partial result returned")
        collection.append(value)

    def evidence(index, rule):
        return {"rule": rule, "source_sentences": texts[index]["source_sentences"],
                "text": texts[index]["text"]}

    for index, item in enumerate(texts):
        question = _question_kind(item["tail"])
        introduction = bool(_INTRODUCTION.search(item["tail"]))
        for kind, rule in (("question_response", question),
                           ("introduction_continuation", "trailing_colon" if introduction else None)):
            if not rule:
                continue
            proof = evidence(index, rule)
            if index + 1 < len(texts):
                add(links, {"first_unit": index, "last_unit": index + 1,
                            "kind": kind, "evidence": proof})
            else:
                add(open_edges, {"unit": index, "side": "right", "kind": kind,
                                 "evidence": proof})
        # Strip quote closers for the question/colon test, but not here: a
        # complete quoted demonstrative is not an unfinished modifier.
        if (not _TERMINAL.search(item["normalized"])
                and _DEMONSTRATIVE.search(item["normalized"])):
            proof = evidence(index, "unpunctuated_demonstrative_tail")
            if index + 1 < len(texts):
                head = _NOMINAL_HEAD.match(texts[index + 1]["normalized"])
                if head:
                    proof["continuation_head"] = head.group()
                    proof["next_source_sentences"] = texts[index + 1]["source_sentences"]
                    proof["next_text"] = texts[index + 1]["text"]
                    add(links, {"first_unit": index, "last_unit": index + 1,
                                "kind": "modifier_head_continuation", "evidence": proof})
            else:
                proof["ambiguous_standalone_reference"] = True
                add(open_edges, {"unit": index, "side": "right",
                                 "kind": "unresolved_demonstrative", "evidence": proof})

    stack = []
    for unit_index, unit in enumerate(units):
        for sentence in unit["sentences"]:
            text = sentence["text"]
            for offset, symbol in enumerate(text):
                matching_quote_open = bool(stack and (
                    (symbol == "'" and stack[-1]["symbol"] == "'")
                    or (symbol == "’" and stack[-1]["symbol"] == "‘")))
                if symbol in ("'", "‘", "’") and _apostrophe(
                        text, offset, matching_quote_open=matching_quote_open):
                    continue
                point = {"unit": unit_index, "source_sentence": sentence["id"],
                         "character": offset, "symbol": symbol}
                paired_ascii = symbol in ('"', "'")
                closes = (symbol in _CLOSE_QUOTES
                          or (paired_ascii and stack and stack[-1]["symbol"] == symbol))
                if closes:
                    expected = _CLOSE_QUOTES.get(symbol, symbol)
                    if stack and stack[-1]["symbol"] == expected:
                        opening = stack.pop()
                        if opening["unit"] != unit_index:
                            add(links, {"first_unit": opening["unit"], "last_unit": unit_index,
                                        "kind": "paired_quotation",
                                        "evidence": {"rule": "balanced_quote_span",
                                                     "opening": opening, "closing": point}})
                    else:
                        add(open_edges, {"unit": unit_index, "side": "left",
                                         "kind": "quotation_missing_opener",
                                         "evidence": {"rule": "unmatched_quote_closer",
                                                      "closing": point}})
                elif symbol in _OPEN_QUOTES or paired_ascii:
                    if len(stack) >= MAX_QUOTE_DEPTH:
                        raise ValueError("event dependency quotation depth limit exceeded; input was not truncated")
                    stack.append(point)
    for opening in stack:
        add(open_edges, {"unit": opening["unit"], "side": "right",
                         "kind": "quotation_missing_closer",
                         "evidence": {"rule": "unmatched_quote_opener", "opening": opening}})
    return {"version": VERSION,
            "scope": "explicit surface dependencies; not semantic event-completeness verification",
            "semantic_completeness_verified": False,
            "links": links, "open_edges": open_edges,
            "limits": {"max_units": MAX_UNITS, "max_sentences": MAX_SENTENCES,
                       "max_text_characters": MAX_TEXT_CHARACTERS,
                       "max_quote_depth": MAX_QUOTE_DEPTH,
                       "max_dependencies": MAX_DEPENDENCIES},
            "model_requests": 0}
