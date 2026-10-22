"""Paired surface evidence for multi-unit discourse continuity.

This extension deliberately leaves the v1 implementation unchanged.  Its links
are negative cut constraints, not positive event-completeness judgements.  An
answer may itself introduce an unfinished complement: consecutive proven links
form an obligation chain rather than closing after one response unit.

Timing policy belongs to the caller.  Hard pauses, missing ASR and maximum clip
duration must never be overridden to satisfy one of these textual obligations.
"""

import re

from clipforge.decision import event_dependencies as _base


VERSION = "surface-event-dependencies-v2"
MAX_DEPENDENCIES = _base.MAX_DEPENDENCIES
MAX_MATCH_CHARACTERS = 256
_BREAK = re.compile(r"[。！？!?；;\n]")
_ENDING = "。！？!?；;.,， \t\r\n"
_NEW_TOPIC = re.compile(
    r"^(?:(?:好(?:的)?|那么|那|接着|所以|因此|因而|于是|so\b|therefore\b)[，,\s]*)?"
    r"(?:(?:我们|你们|他们|她们|大家|我|你|您|他|她)"
    r"(?:(?:就|先|现在|会|想|要|准备|打算)){0,3}[，,\s]*)?"
    r"(?:下面|接下来|另一个话题|另外一个话题|换(?:个|一个)?话题|再说一个话题|至于|说到另一个|"
    r"next\s+(?:topic|subject)\b|moving\s+on\b)", re.I)
_META = re.compile(r"^(?:这个词|这句话|的意思|这个说法|这个表达|the word\b|the phrase\b)", re.I)
_NEGATED_INTENT = re.compile(r"(?:并不|不再|不是|没有|无需|不必|不用)[^。！？!?；;\n]{0,12}$")
_SETTLED = re.compile(
    r"(?:但|不过|后来|而)[^。！？!?；;\n]{0,24}"
    r"(?:已经|早已|都|也)?(?:查清|知道|明白|弄清|了解|解决)|"
    r"(?:(?:答案是|原因是|是因为|因为)(?!(?:什么|啥|何|哪))|"
    r"because\b(?!\s+(?:what|why)\b)|the answer is\b(?!\s+what\b))", re.I)
_INTENT = re.compile(
    r"(?:我|我们)(?:(?:就|会|还|也|其实|很|蛮|仍然|一直|比较|挺|特别|非常|确实|有点)){0,4}"
    r"(?:好奇|想知道|想问)(?P<target>[^。！？!?；;\n]{1,80})$")
_REASON = re.compile(
    r"^(?:(?:我觉得|我认为)[，,\s]*)?"
    r"(?:是因为|主要原因是|原因是|因为|由于|because\b)[，,\s]*(?P<body>.+)$", re.I)
_CAUSE = re.compile(r"^(?:因为|由于|because\b)\s*(?P<body>.+)$", re.I)
_INLINE_RESULT = re.compile(r"(?:所以|因此|因而|于是|导致|以至于|结果(?:是|就)|so\b|therefore\b)", re.I)
_RESULT = re.compile(r"^(?:所以|因此|因而|于是|导致|结果是|therefore\b|so\b)[，,\s]*(?P<body>.+)$", re.I)
_ASPECT = re.compile(
    r"(?:(?:我们|你们|他们|她们|它们|我|你|您|他|她|它|大家)"
    r"(?:(?:目前|现在|最近|主要|就|也|还|一直|其实|可能)){0,3}|^)"
    r"在[^。！？!?；;，,\n]{1,24}(?:方面|上面)$")
_PREDICATE = re.compile(
    r"^(?:得(?:了|到)|遇(?:到|见)|有(?:了|一些|些)|获得|面临|产生|受(?:到)|"
    r"变(?:得|成)|出现|增加|减少|感觉|发现|经历|需要|可以|能够|不能|会|没法|无法)")
_DEFINITION = re.compile(
    r"就是[^。！？!?；;，,\n]{0,60}"
    r"(?:一些|几个|这些|那些)(?P<head>[^。！？!?；;，,\n]{1,20})$")
_FINITE_HEAD = re.compile(r"(?:了|过|着|已经|但是|然后|所以|因为|表示|认为|说)$")
_DEFINES = re.compile(r"^是(?!(?:的|啊|呀|吧|吗|新话题|另一个话题)(?:[。！？!?；;，,\s]|$))(?P<body>.+)$")
_COPULA = re.compile(r"就是$")
_CONTRAST = re.compile(r"反而$")
_CONTRAST_HEAD = re.compile(r"^(?:不|没|更|会|是|影响|让|导致|增加|减少|变|获得|失去)")
_ENUMERATED_REASON = re.compile(
    r"^(?:另外一个|另一个|另一方面|还有一个原因|第二个原因|另外一点)(?:是|原因是|就是)(?P<body>.+)$")
_CONTRAST_REASON = re.compile(r"^(?:但是|但|不过)因为(?P<body>.+)$")
_RANGE_QUESTION = re.compile(r"(?:哪(?:个|一)?(?:方面|领域|范围|方向|部分)|where\b)", re.I)
_FILLER = re.compile(r"^(?:嗯|呃|额|啊|唔|um|uh|erm)[。！？!?；;，,\s]*$", re.I)
_TERMINAL = re.compile(r"[。！？!?；;.]\s*$")


def _mask_quotes(text, stack):
    """Keep source offsets while avoiding new rules inside quoted language."""
    opening = {"“": "”", "‘": "’", "「": "」", "『": "』"}
    closing = set(opening.values())
    output = []
    for index, symbol in enumerate(text):
        ascii_quote = symbol in ('"', "'")
        apostrophe = symbol in ("'", "’") and _base._apostrophe(
            text, index, matching_quote_open=bool(stack and stack[-1] == symbol))
        was_quoted = bool(stack)
        if not apostrophe:
            if stack and symbol == stack[-1]:
                stack.pop()
            elif symbol in opening:
                stack.append(opening[symbol])
            elif ascii_quote:
                stack.append(symbol)
            elif symbol in closing:
                # v1 records unmatched closers; do not infer an intent in it.
                output.append(" ")
                continue
        output.append("\n" if symbol == "\n" else
                      " " if was_quoted or stack else symbol)
    return "".join(output)


def _last_clause(text):
    end = len(text.rstrip(_ENDING))
    start = 0
    for match in _BREAK.finditer(text, 0, end):
        start = match.end()
    while start < end and text[start].isspace():
        start += 1
    # A bounded suffix intentionally declines unknown long constructions.
    bounded_start = max(start, end - MAX_MATCH_CHARACTERS)
    return bounded_start, end, text[bounded_start:end], bounded_start != start


def _substantive(text, *, allow_single_cjk=False):
    stripped = text.strip(_ENDING)
    content = re.sub(r"\W", "", stripped)
    one_cjk = (allow_single_cjk and re.fullmatch(r"[\u3400-\u9fff]", content)
               and content not in "的了着是吧啊吗呢哦嗯呃额唔")
    return ((len(content) >= 2 or one_cjk)
            and not _FILLER.fullmatch(stripped) and not _META.match(stripped))


def _head(text):
    start = len(text) - len(text.lstrip())
    return start, text[start:start + MAX_MATCH_CHARACTERS]


def _point(units, texts, index, start, end):
    """A short verbatim excerpt with offsets into original source sentences."""
    source_spans, cursor = [], 0
    for sentence in units[index]["sentences"]:
        stop = cursor + len(sentence["text"])
        first, last = max(start, cursor), min(end, stop)
        if first < last:
            source_spans.append({"source_sentence": sentence["id"],
                                 "start_character": first - cursor,
                                 "end_character": last - cursor})
        cursor = stop + 1
    return {"unit": index,
            "source_sentences": [row["source_sentence"] for row in source_spans],
            "character_span": [start, end],
            "text": texts[index]["text"][start:end],
            "source_spans": source_spans}


def _obligations(count, links, edges):
    """Union overlapping link intervals in linear space; refer to proofs once."""
    starts = [[] for _ in range(count)]
    edge_units = [[] for _ in range(count)]
    for index, link in enumerate(links):
        starts[link["first_unit"]].append(index)
    for index, edge in enumerate(edges):
        if edge["kind"] != "unresolved_demonstrative":
            edge_units[edge["unit"]].append(index)
    result, active = [], None
    for unit in range(count):
        if active is not None and unit > active["last_unit"]:
            result.append(active)
            active = None
        if not starts[unit] and not edge_units[unit]:
            continue
        if active is None:
            active = {"first_unit": unit, "last_unit": unit,
                      "link_indices": [], "open_edge_indices": [],
                      "closure": "no_further_surface_dependency_detected",
                      "semantic_completeness_verified": False}
        for index in starts[unit]:
            active["link_indices"].append(index)
            active["last_unit"] = max(active["last_unit"], links[index]["last_unit"])
        active["open_edge_indices"].extend(edge_units[unit])
        if edge_units[unit]:
            active["closure"] = "unresolved_surface_dependency"
    if active is not None:
        result.append(active)
    return result


def build_event_continuity(units):
    """Extend v1 cut constraints using paired adjacent discourse constructions.

    ``links`` retain v1 rows as an unmodified prefix.  A newly detected strong
    unfinished grammatical slot with no matching continuation produces an
    ``open_edges`` row.  A source-final intent also remains unresolved;
    unpaired ordinary cause, interior intent and nominal definitions are ``ambiguities``
    because they may already be self-contained assertions.  Continuations do
    not jump across substantive units or search future text for a convenient
    keyword.  No semantic success label or inferred replacement text is emitted.
    """
    original = _base.build_event_dependencies(units)
    texts = _base._validated_units(units)
    count = len(texts)
    quotation_stack = []
    masked = [_mask_quotes(row["text"], quotation_stack) for row in texts]
    clauses = [_last_clause(text) for text in masked]
    links = list(original["links"])
    edges = list(original["open_edges"])
    ambiguities = []
    answers = {row["last_unit"] for row in links if row["kind"] == "question_response"}
    range_answers = {row["last_unit"] for row in links
                     if row["kind"] == "question_response"
                     and _RANGE_QUESTION.search(masked[row["first_unit"]])}
    answer_links = {}
    for link_index, row in enumerate(links):
        if row["kind"] == "question_response":
            answer_links.setdefault(row["last_unit"], []).append(link_index)

    def activate_answer(unit):
        answers.add(unit)
        answer_links.setdefault(unit, []).append(len(links) - 1)

    def reserve():
        if len(links) + len(edges) + len(ambiguities) >= MAX_DEPENDENCIES:
            raise ValueError("event continuity evidence limit exceeded; no partial result returned")

    if len(links) + len(edges) > MAX_DEPENDENCIES:
        raise ValueError("event continuity evidence limit exceeded; no partial result returned")

    def point(index, first, last):
        return _point(units, texts, index, first, last)

    def pair(index, first, last, kind, rule, response, *, required=True):
        reserve()
        proof = {"rule": rule, "left": point(index, first, last),
                 "semantic_completeness_verified": False}
        next_index = index + 1
        if next_index < count:
            offset, head = _head(masked[next_index])
            match = response(head) if not _NEW_TOPIC.match(head) else None
            if match is not None:
                begin, stop = match
                proof["right"] = point(next_index, offset + begin, offset + stop)
                links.append({"first_unit": index, "last_unit": next_index,
                              "kind": kind, "evidence": proof})
                return next_index
        row = {"unit": index, "side": "right", "kind": kind, "evidence": proof}
        if required:
            row["evidence"]["rule"] = "unresolved_" + rule
            edges.append(row)
        else:
            row["retention"] = "warning_only_ambiguous_construction"
            ambiguities.append(row)
        return None

    def matched(pattern, head, *, substantive_body=False):
        match = pattern.match(head)
        if (match is None or not _substantive(head)
                or _META.match(head) or head.startswith(("是的", "是啊", "是呀"))):
            return None
        if substantive_body and not _substantive(match.group("body"), allow_single_cjk=True):
            return None
        # Save the bounded matching head, not the whole following unit/chain.
        return 0, len(head.rstrip())

    for index, (first, last, clause, clipped) in enumerate(clauses):
        if clipped:
            reserve()
            ambiguities.append({"unit": index, "kind": "long_clause_not_parsed",
                                "retention": "warning_only_ambiguous_construction",
                                "evidence": {"rule": "bounded_clause_context_declined",
                                             "left": point(index, first, last),
                                             "semantic_completeness_verified": False}})

    # Resolve explicit reason answers before looking for pending cause clauses:
    # a cause can be a complete answer to "why", without a following "therefore".
    for index, (first, last, clause, clipped) in enumerate(clauses):
        if clipped:
            continue
        intent = _INTENT.search(clause)
        if intent is None or _SETTLED.search(intent.group("target")):
            continue
        if _NEGATED_INTENT.search(clause[:intent.start()]):
            continue
        target = intent.group("target").strip(_ENDING)
        if not _substantive(target, allow_single_cjk=True) or target in ("这个词", "这句话", "这个说法"):
            continue
        answer = pair(index, first + intent.start(), last,
                      "implicit_question_reason", "intent_and_explicit_reason",
                      lambda head: matched(_REASON, head, substantive_body=True),
                      required=index + 1 == count)
        if answer is not None:
            activate_answer(answer)

    for index, (first, last, clause, clipped) in enumerate(clauses):
        if not clause or clipped:
            continue
        # The incoming reason may share its indivisible unit with an explicit
        # topic switch.  That switch ends the answer context before any next
        # unit can be interpreted as another reason in the old answer.
        if _NEW_TOPIC.match(clause):
            answers.discard(index)
            answer_links.pop(index, None)
        cause = _CAUSE.match(clause)
        # The first response can be in this very unit.  It does not require a
        # second result unit merely because its answer starts with "because".
        if (cause is not None and first > 0
                and _base._question_kind(masked[index][:first].strip())):
            answers.add(index)
            answer_links.setdefault(index, [])
        # A finished-sounding first reason does not close an active answer if
        # the next unit explicitly supplies another reason or causal contrast.
        # The incoming answer context is the second, independent piece of proof.
        if index in answers and index + 1 < count:
            offset, head = _head(masked[index + 1])
            kind, pattern = ("reason_enumeration", _ENUMERATED_REASON)
            if _CONTRAST_REASON.match(head):
                kind, pattern = "contrast_cause_continuation", _CONTRAST_REASON
            if (not _NEW_TOPIC.match(head) and not re.search(r"[?？]", head)
                    and matched(pattern, head, substantive_body=True) is not None):
                continuation = pair(index, first, last, kind,
                                    "active_answer_and_" + kind,
                                    lambda value, pattern=pattern:
                                    matched(pattern, value, substantive_body=True))
                if continuation is not None:
                    links[-1]["evidence"]["context_link_indices"] = list(answer_links[index])
                    activate_answer(continuation)
        if (cause is not None and index not in answers
                and _substantive(cause.group("body"), allow_single_cjk=True)
                and not _INLINE_RESULT.search(cause.group("body"))
                and not _TERMINAL.search(masked[index])
                and not _META.match(cause.group("body").lstrip())):
            continuation = pair(index, first, last, "cause_result", "pending_cause_and_result",
                                lambda head: matched(_RESULT, head, substantive_body=True),
                                required=False)
            if continuation is not None:
                activate_answer(continuation)

        aspect = _ASPECT.search(clause)
        definition = _DEFINITION.search(clause)
        copula = _COPULA.search(clause)
        contrast = _CONTRAST.search(clause)
        if aspect is not None:
            continuation = pair(index, first + aspect.start(), last, "predicate_complement",
                                "aspect_and_predicate", lambda head: matched(_PREDICATE, head),
                                required=not (index in range_answers
                                              and _TERMINAL.search(masked[index])))
        elif definition is not None and not _FINITE_HEAD.search(definition.group("head")):
            continuation = pair(index, first + definition.start(), last, "definition_continuation",
                                "nominal_definition_and_copula",
                                lambda head: matched(_DEFINES, head, substantive_body=True), required=False)
        elif copula is not None and _substantive(clause[:copula.start()]):
            continuation = pair(index, first + copula.start(), last, "copular_continuation",
                                "pending_copula_and_complement",
                                lambda head: (0, len(head.rstrip())) if _substantive(head)
                                and not _META.match(head) and not head.startswith(("是的", "是啊", "是呀")) else None)
        elif contrast is not None and _substantive(clause[:contrast.start()]):
            continuation = pair(index, first + contrast.start(), last, "contrast_continuation",
                                "pending_contrast_and_predicate",
                                lambda head: matched(_CONTRAST_HEAD, head))
        else:
            continuation = None
        if index in answers and continuation is not None:
            links[-1]["evidence"]["context_link_indices"] = list(answer_links[index])
            activate_answer(continuation)

    obligations = _obligations(count, links, edges)
    return {"version": VERSION,
            "scope": "paired surface obligations; not semantic event-completeness verification",
            "semantic_completeness_verified": False,
            "links": links, "open_edges": edges, "ambiguities": ambiguities,
            "obligations": obligations,
            "limits": {**original["limits"], "max_dependencies": MAX_DEPENDENCIES,
                       "max_match_characters": MAX_MATCH_CHARACTERS},
            "base_version": original["version"], "model_requests": 0}
