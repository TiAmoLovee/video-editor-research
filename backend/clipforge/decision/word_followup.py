"""Editable word-boundary proposals; no automatic semantics or borrowed scores."""
from copy import deepcopy
import math
import re

from clipforge.analysis.sentences import _units
from clipforge.decision.candidates import content_hash
from clipforge.decision.evaluation import VERSION as BATCH_VERSION
from clipforge.decision.scoring import validate_scored

VERSION = 'editorial-word-boundary-v1'


def freeze_word_followup(analysis, scored, source_name, edits, followup_of):
    validate_scored(scored, analysis)
    keys = {'parent_batch_id','parent_manifest_sha256','parent_review_sha256','reason'}
    if (not isinstance(followup_of, dict) or set(followup_of) != keys
            or any(not isinstance(followup_of[k], str) or not re.fullmatch('[a-f0-9]{64}', followup_of[k])
                   for k in keys-{'reason'})
            or not isinstance(followup_of['reason'], str) or not 1 <= len(followup_of['reason']) <= 2000
            or not isinstance(edits, list) or not 1 <= len(edits) <= 12):
        raise ValueError('需要有效的原评审引用和有限的修正草案')
    words = analysis['words']
    indices = {w['id']:i for i,w in enumerate(words)}
    units = _units(words)
    starts = {u['words'][0]['id'] for u in units}
    ends = {u['words'][-1]['id'] for u in units}
    candidates = {c['id']:c for c in scored['candidates']}
    samples, seen = [], set()
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'label','parent_candidate_id','first_word_id','last_word_id','reason'}
                or any(not isinstance(v, str) or not v.strip() for v in edit.values())
                or edit['parent_candidate_id'] not in candidates
                or edit['first_word_id'] not in starts or edit['last_word_id'] not in ends):
            raise ValueError('修正必须引用已有候选及可追溯的完整词语边界')
        lo, hi = indices[edit['first_word_id']], indices[edit['last_word_id']]
        if lo > hi:
            raise ValueError('词语范围顺序无效')
        members = words[lo:hi+1]
        start, end = min(w['start'] for w in members), max(w['end'] for w in members)
        parent = candidates[edit['parent_candidate_id']]
        first, last = math.floor(start*30+1e-8), math.ceil(end*30-1e-8)
        if (not parent['start'] <= start < end <= parent['end'] or not 15 <= (last-first)/30 <= 90
                or any(w['start'] < end and w['end'] > start for w in words[:lo]+words[hi+1:])
                or (first,last) in seen):
            raise ValueError('修正越过原候选、时长限制、相邻词或重复范围')
        seen.add((first,last))
        refs = [w['id'] for w in members]
        ref_set = set(refs)
        sentence_refs = [s['id'] for s in analysis['sentences'] if ref_set.intersection(s['word_ids'])]
        text = ''.join(w['text'] for w in members).strip()
        identity = dict(version=VERSION, analysis_sha256=content_hash(analysis),
                        parent_candidate_id=parent['id'], source_words=refs, start_frame=first, end_frame=last)
        sid = f'S{len(samples)+1:02}'
        samples.append(dict(id=sid, candidate_id='wc_'+content_hash(identity),
                            parent_candidate_id=parent['id'], label=edit['label'],
                            original_start=start, original_end=end, start_frame=first,
                            end_frame_exclusive=last, fps=30, duration_seconds=(last-first)/30,
                            source_words=refs, source_sentences=sentence_refs, text=text, file=sid+'.mp4',
                            original_score=None, original_rank=None, scorer=None))
    plan = dict(version=BATCH_VERSION, source_name=source_name,
                source_sha256=analysis['media']['source_sha256'],
                normalized_sha256=analysis['media']['normalized_sha256'],
                analysis_sha256=content_hash(analysis), candidates_sha256=content_hash(scored),
                selection_version=VERSION, selection_config={},
                cohort='editorial_followup_not_baseline', presentation_order='explicit_editorial_order',
                scope='Assistant context proposals, not automatic segmentation or a new baseline',
                denominator=len(samples), required_passes=None,
                metric='Per-version human judgment only; no automatic quality-rate claim',
                rounding='outward <= one frame; word timestamps may be imperfect; audition required',
                samples=samples, word_edits=deepcopy(edits), followup_of=deepcopy(followup_of),
                model_requests=0, original_score_reused=False)
    return dict(batch_id=content_hash(plan), **plan)
