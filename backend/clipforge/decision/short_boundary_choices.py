"""Opt-in local choice lists. Legal timing is not semantic acceptance."""
import math

from clipforge.analysis.sentences import END_MARK, _units
from clipforge.decision.candidates import content_hash
from clipforge.decision.context_proposals import discourse_anchors
from clipforge.decision.question_cues import question_context

VERSION = 'short-boundary-choices-v1'


def build_choices(analysis, candidates, *, exhaustive=False, max_options_per_candidate=4096):
    """Use transcript-only cues, never human labels or model-provided times."""
    if type(exhaustive) is not bool:
        raise ValueError('exhaustive must be a boolean')
    if type(max_options_per_candidate) is not int or not 1 <= max_options_per_candidate <= 10000:
        raise ValueError('max options must be an integer from 1 to 10000')
    units = _units(analysis['words'])
    anchors = {a['word_id'] for a in discourse_anchors(analysis['words'])
               if not question_context(analysis['words'], a)}
    ids = [c['id'] for c in candidates]
    if not ids or any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('unique candidate IDs required')
    result = []

    def hint(i, edge):
        cut = i if edge == 'start' else i + 1
        if cut == 0 or cut == len(units):
            return 3  # Media speech endpoint, not proof of completeness.
        if units[cut]['words'][0]['id'] in anchors:
            return 3
        if END_MARK.search(units[cut-1]['text'].rstrip()):
            return 2
        return 1 if units[cut]['start'] - units[cut-1]['end'] >= .15 else 0

    def edges(time, edge):
        eligible = [i for i, u in enumerate(units) if abs(u[edge] - time) <= 6 + 1e-8]
        if exhaustive:
            return eligible
        if not eligible:
            return []
        nearest = min(eligible, key=lambda i: (abs(units[i][edge]-time), i))
        ranked = sorted((i for i in eligible if i != nearest),
                        key=lambda i: (-hint(i, edge), abs(units[i][edge]-time), i))
        return sorted([nearest] + ranked[:3])

    for c in candidates:
        start, end = c['start'], c['end']
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
               for t in (start, end)) or not 0 <= start < end:
            raise ValueError('invalid candidate times')
        options, seen = [], set()
        starts, ends = edges(start, 'start'), edges(end, 'end')
        if exhaustive and len(starts) * len(ends) > 100000:
            raise ValueError('boundary search limit exceeded; no partial shortlist returned')
        for lo in starts:
            for hi in ends:
                if lo > hi:
                    continue
                selected = units[lo:hi+1]
                a, b = min(u['start'] for u in selected), max(u['end'] for u in selected)
                first, last = math.floor(a*30+1e-8), math.ceil(b*30-1e-8)
                if ((a == start and b == end) or abs(a-start) > 6+1e-8 or abs(b-end) > 6+1e-8
                        or b-a > min(end-start+6, max(45, end-start))+1e-8
                        or not 15 <= (last-first)/30 <= 90):
                    continue
                if any(u['start'] < b and u['end'] > a for u in units[:lo]+units[hi+1:]):
                    continue
                if any(y['start']-x['end'] > 3 for x, y in zip(selected, selected[1:])):
                    continue
                if (first, last) in seen or (first, last) == (math.floor(start*30+1e-8), math.ceil(end*30-1e-8)):
                    continue
                seen.add((first, last))
                options.append(dict(id=f'O{len(options)+1:02d}', first_unit=lo+1, last_unit=hi+1,
                                    start=a, end=b, start_frame=first, end_frame_exclusive=last,
                                    duration_seconds=(last-first)/30, score=None, human_pass=None))
                if exhaustive and len(options) > max_options_per_candidate:
                    raise ValueError('option limit exceeded; no partial shortlist returned')
        result.append(dict(id=c['id'], start=start, end=end, options=options))
    report = dict(version=VERSION, analysis_sha256=content_hash(analysis),
                units=[[i, u['text']] for i, u in enumerate(units, 1)], candidates=result,
                config=dict(max_edge_shift=6, max_growth=6, short_ceiling=45, edges_per_side=4),
                automatic_acceptance=False, semantic_completeness_verified=False,
                scope='Development shortlist; may omit useful boundaries. No independent quality claim.')
    if exhaustive:
        report.update(version='short-boundary-choices-v2',
                      scope='All legal tokenizer-boundary frame ranges within the fixed timing limits. '
                            'Coverage is not semantic completeness or independent quality evidence.')
        report['config'].update(edges_per_side=None, exhaustive=True,
                                max_options_per_candidate=max_options_per_candidate,
                                max_pairs_per_candidate=100000)
    return report


def compact_choices(plan):
    """Lossless whitelist: first-unit ID -> inclusive runs of legal last-unit IDs.

    Time values stay local. Gaps caused by duration/overlap checks are never filled.
    This is a transport representation, not a new shortlist or semantic ranking.
    """
    rows = []
    for c in plan['candidates']:
        groups = {}
        for o in c['options']:
            groups.setdefault(o['first_unit'], set()).add(o['last_unit'])
        encoded = []
        for first, ends in sorted(groups.items()):
            runs = []
            for last in sorted(ends):
                if runs and last == runs[-1][1]+1:
                    runs[-1][1] = last
                else:
                    runs.append([last, last])
            encoded.append([first, runs])
        rows.append(dict(id=c['id'], allowed_pairs=encoded))
    return dict(version='boundary-pair-runs-v1', plan_sha256=content_hash(plan), candidates=rows)


def resolve_compact_choices(plan, response):
    """Accept only exact local pairs, never independent start/end permissions."""
    if not isinstance(response, dict) or set(response) != {'items'}:
        raise ValueError('expected items only')
    rows = response['items']
    if not isinstance(rows, list) or len(rows) != len(plan['candidates']):
        raise ValueError('all candidate rows required')
    converted = []
    for row, c in zip(rows, plan['candidates']):
        if not isinstance(row, list) or len(row) != 4 or row[0] != c['id']:
            raise ValueError('invalid row or order')
        _, status, first, last = row
        if status in ('keep', 'uncertain') and first is None and last is None:
            converted.append([c['id'], status])
        elif status == 'adjust' and type(first) is int and type(last) is int:
            option = next((o for o in c['options'] if (o['first_unit'], o['last_unit']) == (first, last)), None)
            if option is None:
                raise ValueError('boundary pair not in local whitelist')
            converted.append([c['id'], option['id']])
        else:
            raise ValueError('invalid status or unit IDs')
    return resolve_choices(plan, {'items': converted})


def resolve_choices(plan, response):
    """Map IDs to immutable local ranges; fail closed on every malformed batch."""
    if not isinstance(response, dict) or set(response) != {'items'}:
        raise ValueError('expected items only')
    rows = response['items']
    if not isinstance(rows, list) or len(rows) != len(plan['candidates']):
        raise ValueError('all candidate rows required')
    resolved = []
    for row, c in zip(rows, plan['candidates']):
        if (not isinstance(row, list) or len(row) != 2 or row[0] != c['id']
                or not isinstance(row[1], str)):
            raise ValueError('invalid row or order')
        if row[1] in ('keep', 'uncertain'):
            resolved.append(dict(id=c['id'], status=row[1], start=c['start'], end=c['end'],
                                 score=None, human_pass=None))
        else:
            option = next((x for x in c['options'] if x['id'] == row[1]), None)
            if option is None:
                raise ValueError('unknown option for candidate')
            resolved.append(dict(option, id=c['id'], option_id=option['id'], status='adjust'))
    return resolved
