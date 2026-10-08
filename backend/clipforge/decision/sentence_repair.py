"""Source-independent sentence repair proposals; no labels or network access.

Punctuation from an untrusted model is projected through unique unchanged text.
Every original candidate remains in the result, even with no usable alternatives.
"""
import difflib
import math
import unicodedata

from clipforge.analysis.sentences import _units
from clipforge.decision.candidates import content_hash

VERSION = 'sentence-repair-v1'


def _characters(text):
    # Keep symbols (e.g. + in C++); only punctuation and whitespace are ignorable.
    return ''.join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith('P'))


def project_punctuation(words, punctuated):
    if not isinstance(punctuated, str) or not punctuated or len(punctuated) > 100000:
        raise ValueError('invalid punctuation response size')
    units = _units(words)
    original = ''.join(u['text'] for u in units)
    source, proposed = _characters(original), _characters(punctuated)
    if not source or len(source) > 50000:
        raise ValueError('unsupported transcript size')
    matcher = difflib.SequenceMatcher(a=source, b=proposed, autojunk=False)
    if matcher.ratio() < .98:
        raise ValueError('too many source edits; do not use punctuation')
    blocks = matcher.get_matching_blocks()
    offsets = {}
    position = 0
    for i, u in enumerate(units, 1):
        position += len(_characters(u['text']))
        offsets.setdefault(position, []).append(i)
    boundaries = {len(units): dict(after_unit=len(units), kind='source_end_not_semantic_verdict')}
    cursor = 0
    for char in punctuated:
        if _characters(char):
            cursor += 1
        elif char in '。！？!?':
            mapped = []
            for block in blocks:
                width = min(24, cursor-block.b, block.b+block.size-cursor)
                if width < 6:
                    continue
                anchor = proposed[cursor-width:cursor+width]
                if source.count(anchor) != 1 or proposed.count(anchor) != 1:
                    continue
                source_position = block.a+cursor-block.b
                if len(offsets.get(source_position, [])) == 1:
                    mapped.append((offsets[source_position][0], anchor))
            if len(mapped) == 1:
                after, anchor = mapped[0]
                boundaries[after] = dict(after_unit=after, kind='projected_punctuation',
                                         punctuation=char, unchanged_context=anchor)
    changes = [dict(kind=tag, original=source[i:j], model=proposed[k:l])
               for tag, i, j, k, l in matcher.get_opcodes() if tag != 'equal']
    return dict(source_text=original, boundaries=[boundaries[i] for i in sorted(boundaries)],
                model_text_changes_not_adopted=changes, semantic_completeness_verified=False)


def build_sentence_choices(analysis, candidates, punctuated, *, max_options=4096):
    if type(max_options) is not int or not 1 <= max_options <= 10000:
        raise ValueError('invalid option cap')
    ids = [c['id'] for c in candidates]
    if not ids or len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('unique candidate IDs required')
    projection = project_punctuation(analysis['words'], punctuated)
    units = _units(analysis['words'])
    cuts = {b['after_unit'] for b in projection['boundaries']}
    result = []
    for c in candidates:
        start, end = c['start'], c['end']
        if (any(isinstance(t, bool) or not isinstance(t, (float, int)) or not math.isfinite(t) for t in (start, end))
                or not 0 <= start < end):
            raise ValueError('invalid original times')
        starts = [i for i, u in enumerate(units) if u['start'] == start]
        ends = [i+1 for i, u in enumerate(units) if u['end'] == end]
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('original range must have unambiguous complete-unit boundaries')
        original_frames = (math.floor(start*30+1e-8), math.ceil(end*30-1e-8))
        opening_cuts = sorted(i for i in {0, *cuts, starts[0]} if i < len(units) and abs(units[i]['start']-start) <= 30)
        ending_cuts = sorted(i for i in {*cuts, ends[0]} if abs(units[i-1]['end']-end) <= 30)
        if len(opening_cuts)*len(ending_cuts) > 100000:
            raise ValueError('search cap exceeded; no partial cohort returned')
        options, seen = [], set()
        for lo in opening_cuts:
            for hi in ending_cuts:
                if lo >= hi:
                    continue
                members = units[lo:hi]
                a, b = min(u['start'] for u in members), max(u['end'] for u in members)
                frames = (math.floor(a*30+1e-8), math.ceil(b*30-1e-8))
                overlap = max(0, min(b, end)-max(a, start))
                if (frames == original_frames or frames in seen or not 15 <= (frames[1]-frames[0])/30 <= 90
                        or abs(a-start) > 30 or abs(b-end) > 30 or b-a > end-start+30
                        or overlap < .35*min(b-a, end-start)):
                    continue
                if any(u['start'] < b and u['end'] > a for u in units[:lo]+units[hi:]):
                    continue
                if any(right['start']-left['end'] > 3 for left, right in zip(members, members[1:])):
                    continue
                seen.add(frames)
                options.append(dict(id=f'O{len(options)+1:04}', start=a, end=b,
                    start_frame=frames[0], end_frame_exclusive=frames[1],
                    duration_seconds=(frames[1]-frames[0])/30,
                    first_word_id=members[0]['words'][0]['id'], last_word_id=members[-1]['words'][-1]['id'],
                    text=''.join(u['text'] for u in members), score=None, human_pass=None))
                if len(options) > max_options:
                    raise ValueError('option cap exceeded; no partial cohort returned')
        result.append(dict(id=c['id'], start=start, end=end, original_frames=list(original_frames),
                           original_text=''.join(u['text'] for u in units[starts[0]:ends[0]]), options=options))
    return dict(version=VERSION, analysis_sha256=content_hash(analysis), projection=projection,
                candidates=result, automatic_acceptance=False,
                policy=dict(max_edge_shift_seconds=30, max_growth_seconds=30, preferred_seconds=45,
                            minimum_seconds=15, maximum_seconds=90, minimum_overlap_fraction=.35,
                            max_options=max_options, human_labels_used=False))


def selection_packet(plan, candidate_id):
    """One original at a time. Never truncate a large pool invisibly."""
    candidate = next(c for c in plan['candidates'] if c['id'] == candidate_id)
    return dict(candidate_id=candidate_id, source_text=plan['projection']['source_text'],
                original=dict(text=candidate['original_text'], start=candidate['start'], end=candidate['end']),
                options=[dict(id=o['id'], text=o['text'], duration_seconds=o['duration_seconds']) for o in candidate['options']])


def resolve_selections(plan, responses):
    """Unresolved/colliding decisions retain a slot; no quality label is inferred."""
    if set(responses) != {c['id'] for c in plan['candidates']}:
        raise ValueError('every original slot requires a decision')
    rows = []
    for c in plan['candidates']:
        response = responses[c['id']]
        if (not isinstance(response, dict) or set(response) != {'choice', 'reason'}
                or not isinstance(response['reason'], str) or not isinstance(response['choice'], str)):
            raise ValueError('invalid selection response')
        choice = response['choice']
        if choice in ('keep', 'uncertain'):
            selected = dict(start=c['start'], end=c['end'], start_frame=c['original_frames'][0],
                            end_frame_exclusive=c['original_frames'][1])
        else:
            selected = next((o for o in c['options'] if o['id'] == choice), None)
            if selected is None:
                raise ValueError('selection is not a local option')
        rows.append(dict(selected, id=c['id'], decision=choice, reason=response['reason'],
                         human_pass=None, score=None, status='unverified'))
    counts = {}
    for row in rows:
        key = (row['start_frame'], row['end_frame_exclusive'])
        counts[key] = counts.get(key, 0)+1
    for row in rows:
        if counts[(row['start_frame'], row['end_frame_exclusive'])] > 1:
            row['status'] = 'unresolved_duplicate_range'
    return dict(denominator=len(plan['candidates']), items=rows, automatic_acceptance=False)
