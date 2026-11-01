"""Publish aggregate-only results from a completed, validated Week 7 summary.

This supplementary step does not replace the frozen campaign/export validator.
It recomputes its statistics and never changes answers or adjudicates disagreement.
"""
import argparse
import importlib.util
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


HELPER_PATH = Path(__file__).with_name('summarize_human_reviews.py')
SPEC = importlib.util.spec_from_file_location('week7_frozen_summary', HELPER_PATH)
frozen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(frozen)
FIELDS = frozen.FIELDS
LABELS = frozen.LABELS
STRATA = dict(zip(frozen.CLASSIFICATIONS, (21, 9)))
require = frozen.require
SUMMARY_KEYS = frozenset((
    'schema_version', 'campaign_id', 'generated_at', 'scope', 'status', 'all30_ready',
    'all_six_manifests_ready', 'complete', 'coverage_complete', 'fixed_source_denominator',
    'planned_clip_slots', 'actual_rateable_clip_count', 'actual_count_final',
    'unfilled_planned_slots', 'source_outcomes', 'human_review_coverage', 'groups',
    'statistics', 'strata', 'paired_answers', 'input_audit', 'required_action', 'limits'))
ROW_KEYS = frozenset(('sample_id', 'sha256', 'batch_index', 'video_id', 'exposure_stratum', 'A', 'B'))
OUTCOME_KEYS = frozenset(('status_counts', 'failed_sources', 'no_candidate_sources', 'pending_sources',
    'planned_slots_lost_to_source_failure', 'planned_slots_unfilled_no_candidates',
    'planned_slots_unfilled_insufficient_candidates', 'source_failure_is_a_human_rejection'))
COVERAGE_KEYS = frozenset(('expected_nonempty_groups_per_rater', 'completed_nonempty_groups_per_rater',
    'completed_paired_nonempty_groups', 'pooled_paired_rows_available',
    'empty_groups_not_counted_as_completed_human_reviews', 'empty_na_exports_missing_nonblocking',
    'pending_reviews', 'missing_frozen_manifest_groups'))
AUDIT_KEYS = frozenset(('individual_complete_semantic_records', 'in_progress_exports',
    'not_applicable_exports', 'identical_complete_duplicates', 'conflicts', 'invalid_files',
    'ignored_files', 'answers_waiting_for_manifest'))


def exact_keys(value, keys, label):
    require(isinstance(value, dict) and set(value) == keys, 'Unexpected fields: ' + label)


def same(left, right, label):
    # JSON comparison also rejects bools masquerading as integers and extra fields.
    encode = lambda value: json.dumps(value, sort_keys=True, allow_nan=False)
    require(encode(left) == encode(right), 'Inconsistent ' + label)


def counts(value, total, label):
    require(isinstance(value, dict) and set(value) <= frozen.TERMINAL
            and all(type(n) is int and n > 0 for n in value.values())
            and sum(value.values()) == total, 'Invalid source counts: ' + label)
    return Counter(value)


def validate(summary):
    exact_keys(summary, SUMMARY_KEYS, 'summary')
    require(summary['schema_version'] == 'week7-formal30-human-summary-v1'
            and summary['scope'] == 'formal30_mixed_exposure', 'Wrong summary schema/scope')
    require(frozen.is_hash(summary['campaign_id']) and frozen.timestamp(summary['generated_at']),
            'Invalid summary provenance')
    require(summary['status'] == 'complete' and summary['required_action'] is None
            and all(summary[key] is True for key in ('complete', 'coverage_complete', 'all30_ready',
                'all_six_manifests_ready', 'actual_count_final')), 'Only completed summaries can be published')
    same(summary['fixed_source_denominator'], 30, 'source denominator')
    same(summary['planned_clip_slots'], 150, 'planned slots')
    n = summary['actual_rateable_clip_count']
    require(frozen.integer(n, 1, 150), 'Invalid actual clip denominator')
    same(summary['unfilled_planned_slots'], 150 - n, 'unfilled slots')
    rows = summary['paired_answers']
    require(isinstance(rows, list) and len(rows) == n, 'Missing or inconsistent paired answers')
    seen = set()
    sources = defaultdict(list)
    groups = Counter()
    for row in rows:
        exact_keys(row, ROW_KEYS, 'paired row')
        sid = row['sample_id']
        require(isinstance(sid, str) and re.fullmatch(r'R\d{4}', sid)
                and 1 <= int(sid[1:]) <= 150 and sid not in seen, 'Invalid or duplicate sample')
        seen.add(sid)
        group = row['batch_index']
        require(frozen.integer(group, 1, 6) and (int(sid[1:]) - 1) // 25 + 1 == group,
                'Sample/group mismatch')
        require(frozen.is_hash(row['sha256']), 'Invalid sample fingerprint')
        vid = row['video_id']
        require(isinstance(vid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', vid), 'Invalid source identifier')
        require(row['exposure_stratum'] in STRATA, 'Unexpected source stratum')
        for rater in ('A', 'B'):
            exact_keys(row[rater], set(FIELDS), 'answers')
            require(all(value in LABELS for value in row[rater].values()), 'Missing or invalid answer')
        sources[vid].append(row)
        groups[group] += 1
    for source_rows in sources.values():
        require(len(source_rows) <= 5 and len({row['batch_index'] for row in source_rows}) == 1
                and len({row['exposure_stratum'] for row in source_rows}) == 1,
                'Source exceeds five clips or crosses groups/strata')
        require(len({(int(row['sample_id'][1:]) - 1) // 5 for row in source_rows}) == 1,
                'Source crosses reserved slot blocks')
    slot_sources = {((int(row['sample_id'][1:]) - 1) // 5, row['video_id']) for row in rows}
    require(len(slot_sources) == len(sources)
            and len({slot for slot, _ in slot_sources}) == len(sources), 'Source/slot mapping is not one-to-one')
    same(summary['statistics'], frozen.aggregate(rows), 'primary statistics')
    exact_keys(summary['strata'], set(STRATA), 'strata')
    all_counts = Counter()
    for label, source_total in STRATA.items():
        stratum = summary['strata'][label]
        exact_keys(stratum, {'fixed_source_denominator', 'planned_clip_slots', 'actual_published_clip_count',
            'source_status_counts', 'statistics', 'interpretation'}, 'stratum')
        subset = [row for row in rows if row['exposure_stratum'] == label]
        same(stratum['fixed_source_denominator'], source_total, 'stratum source denominator')
        same(stratum['planned_clip_slots'], source_total * 5, 'stratum slots')
        same(stratum['actual_published_clip_count'], len(subset), 'stratum clip count')
        sc = counts(stratum['source_status_counts'], source_total, label)
        same(sc['completed'], len({row['video_id'] for row in subset}), 'stratum completed sources')
        all_counts.update(sc)
        same(stratum['statistics'], frozen.aggregate(subset), 'stratum statistics')
    outcomes = summary['source_outcomes']
    exact_keys(outcomes, OUTCOME_KEYS, 'source outcomes')
    same(dict(counts(outcomes['status_counts'], 30, 'all')), dict(all_counts), 'source outcome totals')
    require(outcomes['source_failure_is_a_human_rejection'] is False and outcomes['pending_sources'] == [],
            'Pending sources or invented human rejections')
    require(isinstance(outcomes['failed_sources'], list) and isinstance(outcomes['no_candidate_sources'], list),
            'Invalid terminal source lists')
    failed = []
    for row in outcomes['failed_sources']:
        exact_keys(row, {'video_id', 'failed_stage'}, 'failed source')
        failed.append(row['video_id'])
    no_candidates = outcomes['no_candidate_sources']
    terminal_ids = list(sources) + failed + no_candidates
    require(all(isinstance(vid, str) for vid in terminal_ids)
            and len(terminal_ids) == len(set(terminal_ids)) == 30, 'Terminal source identity/count mismatch')
    same(len(failed), all_counts['failed'], 'failed source count')
    same(len(no_candidates), all_counts['completed_no_candidates'], 'empty source count')
    same(outcomes['planned_slots_lost_to_source_failure'], 5 * len(failed), 'failed slots')
    same(outcomes['planned_slots_unfilled_no_candidates'], 5 * len(no_candidates), 'empty slots')
    same(outcomes['planned_slots_unfilled_insufficient_candidates'], 5 * len(sources) - n, 'insufficient slots')
    validate_coverage(summary, groups)
    return rows, sources


def validate_coverage(summary, groups):
    coverage = summary['human_review_coverage']
    exact_keys(coverage, COVERAGE_KEYS, 'review coverage')
    nonempty = len(groups)
    expected = {'expected_nonempty_groups_per_rater': nonempty,
        'completed_nonempty_groups_per_rater': {'A': nonempty, 'B': nonempty},
        'completed_paired_nonempty_groups': nonempty, 'pooled_paired_rows_available': sum(groups.values()),
        'empty_groups_not_counted_as_completed_human_reviews': 6 - nonempty,
        'pending_reviews': [], 'missing_frozen_manifest_groups': []}
    for key, value in expected.items():
        same(coverage[key], value, 'review coverage ' + key)
    require(isinstance(summary['groups'], list) and len(summary['groups']) == 6, 'Six group records required')
    indices = set()
    for group in summary['groups']:
        exact_keys(group, {'batch_index', 'batch_id', 'manifest_sha256', 'sample_count', 'raters', 'status'}, 'group')
        index = group['batch_index']
        require(frozen.integer(index, 1, 6) and index not in indices, 'Invalid/duplicate group')
        indices.add(index)
        require(frozen.is_hash(group['batch_id']) and frozen.is_hash(group['manifest_sha256']), 'Invalid group fingerprint')
        same(group['sample_count'], groups[index], 'group denominator')
        same(group['status'], 'complete' if groups[index] else 'not_applicable', 'group status')
        exact_keys(group['raters'], {'A', 'B'}, 'group raters')
        for rater in ('A', 'B'):
            value = group['raters'][rater]
            exact_keys(value, {'status', 'human_review_complete', 'file'} if groups[index]
                else {'status', 'human_review_complete', 'na_export_files'}, 'group rater')
            same(value['status'], group['status'], 'rater status')
            require(value['human_review_complete'] is bool(groups[index]), 'Rater completeness mismatch')
    audit = summary['input_audit']
    exact_keys(audit, AUDIT_KEYS, 'input audit')
    same(audit['individual_complete_semantic_records'], 2 * nonempty, 'complete export count')
    for key in AUDIT_KEYS - {'individual_complete_semantic_records'}:
        require(isinstance(audit[key], list), 'Invalid audit list')
    require(not any(audit[key] for key in ('conflicts', 'invalid_files', 'answers_waiting_for_manifest')),
            'Unresolved invalid or conflicting input')


def supplementary(rows, sources):
    balanced = {}
    for rater in ('A', 'B'):
        source_metrics = [frozen.rater_metrics(items, rater) for items in sources.values()]
        balanced[rater] = {key: sum(value[key]['yes_rate'] for value in source_metrics) / len(sources)
            for key in ('boundary', 'semantic_complete_five_items', 'accept')}
    overlap = Counter(sum(row[rater]['accept'] == 'yes' for rater in ('A', 'B')) for row in rows)
    margins = {rater: {field: {label: sum(row[rater][field] == label for row in rows) for label in LABELS}
        for field in FIELDS} for rater in ('A', 'B')}
    non_yes = {}
    for rater in ('A', 'B'):
        non_yes[rater] = sorted([
            {'question': field, 'no_count': margins[rater][field]['no'],
             'uncertain_count': margins[rater][field]['uncertain'],
             'non_yes_count': margins[rater][field]['no'] + margins[rater][field]['uncertain']}
            for field in FIELDS], key=lambda row: (-row['non_yes_count'], FIELDS.index(row['question'])))
    return {'source_balanced': {'rateable_source_count': len(sources),
        'sources_without_rateable_clips': 30 - len(sources),
        'method': 'Arithmetic mean of per-source yes rates, over sources with rateable clips only.',
        'interpretation': 'Descriptive supplementary means; no independent-sample inference or fabricated rates for empty sources.',
        'per_rater': balanced},
        'accept_overlap': {'fixed_clip_denominator': len(rows), 'both_yes': overlap[2],
            'one_yes': overlap[1], 'neither_yes': overlap[0],
            'interpretation': 'Observed overlap, not consensus or adjudicated truth; neither_yes includes uncertain.'},
        'question_margins': margins, 'questions_by_non_yes_count': non_yes}


def build_report(summary_path):
    summary_path = Path(summary_path)
    upstream_sha = frozen.file_hash(summary_path)
    summary = frozen.read(summary_path)
    rows, sources = validate(summary)
    require(frozen.file_hash(summary_path) == upstream_sha, 'Upstream summary changed while being read')
    strata = {label: {**value,
        'interpretation': 'Descriptive mixed-exposure stratum; not an all-unseen random independent sample.'}
        for label, value in summary['strata'].items()}
    return {'schema_version': 'week7-human-aggregate-report-v1',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'complete',
        'campaign_id': summary['campaign_id'],
        'provenance': {'upstream_schema': summary['schema_version'], 'upstream_generated_at': summary['generated_at'],
            'upstream_summary_sha256': upstream_sha,
            'frozen_summary_tool_sha256': frozen.file_hash(HELPER_PATH),
            'report_tool_sha256': frozen.file_hash(Path(__file__)),
            'scope': 'Completed upstream summary checked for internal consistency; not a new campaign/export revalidation.'},
        'fixed_source_denominator': 30, 'planned_clip_slots': 150,
        'actual_rateable_clip_count': len(rows), 'unfilled_planned_slots': 150 - len(rows),
        'source_outcomes': {key: summary['source_outcomes'][key] for key in (
            'status_counts', 'planned_slots_lost_to_source_failure', 'planned_slots_unfilled_no_candidates',
            'planned_slots_unfilled_insufficient_candidates', 'source_failure_is_a_human_rejection')},
        'completed_paired_group_count': sum(group['sample_count'] > 0 for group in summary['groups']),
        'statistics': summary['statistics'], 'strata': strata,
        'supplementary': supplementary(rows, sources),
        'privacy': {'individual_answers_published': False, 'sample_or_source_identifiers_published': False,
            'original_filenames_or_local_paths_published': False},
        'limits': ['Uncertain remains in the three-class primary agreement and fixed denominators.',
            'Source-balanced rates supplement the original clip-weighted results; no confidence intervals or targets are inferred.',
            'This selected Top5 cohort does not measure full-pool ranking discrimination or compare rule and LLM scorers.',
            'Source failures or unfilled slots are not human no answers. No disagreement is adjudicated.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        require(not args.output.exists(), '--output must name a new file')
        report = build_report(args.summary)
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(2, f'REPORT NOT CREATED: {type(error).__name__}: {error}\n')
    print(json.dumps({'status': 'complete', 'actual_rateable_clip_count': report['actual_rateable_clip_count']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
