"""Render and validate unscored optimized ranges, independently of human acceptance."""
import math
from pathlib import Path
import re

from clipforge.analysis.sentences import _units
from clipforge.decision.boundary_optimizer import VERSION
from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash


def render(report, source, folder, ffmpeg, ffprobe):
    if report['status'] != 'ready':
        report['renders'] = {}
        return {}
    eligible = [i for i in report['items'] if i['status'] == 'unverified']
    report['renders'] = {}
    if not eligible:
        return {}
    from clipforge.decision.evaluation import VERSION as EVALUATION_VERSION, render_plan
    samples = [dict(i, file='repair_'+i['id']+'.mp4', fps=30) for i in eligible]
    plan = {'version': EVALUATION_VERSION, 'source_name': 'boundary-optimized',
            'normalized_sha256': report['normalized_sha256'], 'samples': samples,
            'denominator': len(samples), 'automatic_acceptance': False}
    manifest = render_plan(dict(batch_id=content_hash(plan), **plan), source, folder/'repaired', ffmpeg, ffprobe)
    report['renders'] = manifest['renders']
    return {v['file']: 'repaired/'+v['file'] for v in report['renders'].values()}


def validate_report(report, analysis, scored, folder=None):
    if (report['version'] != VERSION or report['artifact_kind'] != 'boundary_optimization'
            or report['automatic_acceptance'] is not False
            or report['analysis_sha256'] != content_hash(analysis)
            or report['candidates_sha256'] != content_hash(scored)
            or any(report[k] != analysis['media'][k] for k in ('source_sha256', 'normalized_sha256'))
            or report['status'] not in ('ready', 'fallback') or not isinstance(report['items'], list)):
        raise ValueError('optimized boundary identity mismatch')
    if report['status'] == 'fallback':
        if report['items'] or report.get('renders'):
            raise ValueError('partial optimized output must not be published')
        return
    originals = {c['id']: c for c in scored['candidates']}
    ids = report['original_candidate_ids']
    if (len(set(ids)) != len(ids) or set(ids)-set(originals) or report['denominator'] != len(ids)
            or len(report['items']) != len(ids) or {i['candidate_id'] for i in report['items']} != set(ids)):
        raise ValueError('optimized cohort changed')
    units = _units(analysis['words'])
    eligible = set()
    for i in report['items']:
        old = originals[i['candidate_id']]
        first, last = i['start_frame'], i['end_frame_exclusive']
        expected_id = content_hash({'source': report['normalized_sha256'], 'candidate': old['id'], 'first': first, 'last': last})
        members = [u for u in units if u['start'] >= i['start'] and u['end'] <= i['end']]
        if (i['id'] != expected_id or type(first) is not int or type(last) is not int
                or not 0 <= first < last <= math.ceil(analysis['media']['duration_seconds']*30)
                or first != math.floor(i['start']*30+1e-8) or last != math.ceil(i['end']*30-1e-8)
                or i['original_start'] != old['start'] or i['original_end'] != old['end']
                or not 15 <= (last-first)/30 <= 90
                or i['duration_seconds'] != (last-first)/30
                or i['score'] is not None or i['human_pass'] is not None
                or not members or min(u['start'] for u in members) != i['start']
                or max(u['end'] for u in members) != i['end']
                or ''.join(u['text'] for u in members) != i['text']
                or i['status'] not in ('unverified', 'unresolved_model_uncertain', 'unresolved_duplicate_range')):
            raise ValueError('optimized range or label mismatch')
        if i['status'] == 'unverified':
            eligible.add(i['id'])
    renders = report.get('renders', {})
    if set(renders) != eligible:
        raise ValueError('optimized media incomplete')
    for i in report['items']:
        if i['id'] not in renders:
            continue
        evidence = renders[i['id']]
        if (evidence['file'] != 'repair_'+i['id']+'.mp4' or evidence['frames'] != i['end_frame_exclusive']-i['start_frame']
                or evidence['duration_seconds'] != i['duration_seconds']
                or not re.fullmatch('[a-f0-9]{64}', evidence['sha256'])):
            raise ValueError('optimized media evidence mismatch')
        if folder is not None:
            target = (Path(folder)/'repaired'/evidence['file']).resolve()
            if not target.is_relative_to(Path(folder).resolve()) or file_hash(target) != evidence['sha256']:
                raise ValueError('optimized media hash mismatch')


def public_report(report, task_id):
    return {'status': report['status'], 'fallback_reason': report['fallback_reason'],
            'denominator': report['denominator'], 'automatic_acceptance': False,
            'versions': [dict(id=i['id'], candidate_id=i['candidate_id'], start=i['start_frame']/30,
                              end=i['end_frame_exclusive']/30, duration_seconds=i['duration_seconds'],
                              text=i['text'], status=i['status'], score=None, human_pass=None,
                              video_url=f"/tasks/{task_id}/files/{report['renders'][i['id']]['file']}"
                              if i['id'] in report.get('renders', {}) else None)
                         for i in report['items']]}
