"""Score actual new references; preserve fixed denominators and missing labels.

Outputs a private JSON containing a sanitized public_summary. Without submitted
reference files, human-dependent metrics stay null. No models or network.
"""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .metrics import character_errors, combine, cut_match, normalize_text
    from .release_word_timing_form import read, require, timestamp, validate_blind
else:
    from metrics import character_errors, combine, cut_match, normalize_text
    from release_word_timing_form import read, require, timestamp, validate_blind

THRESHOLD = .3


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def endpoint_error(prediction, human, uncertainty, threshold=THRESHOLD):
    require(all(finite_number(x) for x in (prediction, human, uncertainty, threshold)),
            'Finite endpoint numbers required')
    require(uncertainty >= 0 and threshold >= 0, 'Negative uncertainty/threshold')
    point = prediction - human
    low, high = point - uncertainty, point + uncertainty
    worst = max(abs(low), abs(high))
    nearest = max(0, abs(point) - uncertainty)
    status = 'pass' if worst <= threshold + 1e-12 else 'fail' if nearest > threshold + 1e-12 else 'uncertain'
    return {'signed_point_error_seconds': point, 'absolute_point_error_seconds': abs(point),
            'signed_error_interval_seconds': [low, high], 'maximum_absolute_error_bound_seconds': worst,
            'status': status}


def transcript_metrics(items, hypotheses):
    require(len(items) == 3 and len({x['excerpt_id'] for x in items}) == 3,
            'Keep all three transcript excerpts')
    rows = []
    for reference in items:
        id_ = reference['excerpt_id']
        prediction = hypotheses.get(id_)
        row = {'excerpt_id': id_, 'reference_status': reference['reference_status'],
               'human_reference_declared_complete': reference['complete'],
               'machine_prediction_available': prediction is not None, 'character_metrics': None}
        if reference['reference_status'] == 'partially_unintelligible':
            row['metric_status'] = 'not_scored_partial_human_reference'
        elif prediction is None:
            row['metric_status'] = 'not_scored_failed_machine_prediction'
        else:
            require(reference['reference_status'] in ('complete', 'no_speech'), 'Wrong reference status')
            text = normalize_text(reference['text'])
            hypothesis = normalize_text(''.join(w['text'] for w in prediction['words']))
            require(reference['reference_status'] != 'complete' or text, 'Complete spoken reference cannot normalize empty')
            require(reference['reference_status'] != 'no_speech' or not text, 'No-speech reference must be empty')
            row['character_metrics'] = character_errors(text, hypothesis)
            row['metric_status'] = 'scored' if text else 'scored_empty_reference_insertions_reported'
        rows.append(row)
    usable = [x['character_metrics'] for x in rows if x['character_metrics'] is not None]
    reference_chars = sum(x['reference_characters'] for x in usable)
    total_errors = sum(x['edit_distance'] for x in usable)
    all_three = len(usable) == 3
    subset = {'contributing_excerpts': len(usable), 'reference_characters': reference_chars,
              'edit_distance': total_errors,
              'cer': total_errors / reference_chars if reference_chars else None,
              'substitutions': sum(x['substitutions'] for x in usable),
              'deletions': sum(x['deletions'] for x in usable),
              'insertions': sum(x['insertions'] for x in usable)}
    return {'fixed_excerpt_denominator': 3, 'fixed_audio_seconds': 60,
        'full_reference_and_prediction_excerpts': len(usable),
        'partial_human_reference_excerpts': sum(x['reference_status'] == 'partially_unintelligible' for x in rows),
        'no_speech_human_reference_excerpts': sum(x['reference_status'] == 'no_speech' for x in rows),
        'failed_machine_prediction_excerpts': sum(not x['machine_prediction_available'] for x in rows),
        'all_three_eligible': all_three,
        'primary_pooled_cer': subset['cer'] if all_three else None,
        'primary_reference_characters': reference_chars if all_three else None,
        'primary_edit_distance': total_errors if all_three else None,
        'primary_null_reason': None if all_three and reference_chars else
            'all_reference_character_denominators_empty' if all_three else 'some_fixed_excerpts_lack_full_human_reference_or_machine_prediction',
        'available_full_reference_subset_descriptive': subset,
        'partial_text_markers_scored_as_characters': False,
        'empty_reference_policy': 'Human-confirmed no speech has zero reference characters; machine output counts as insertions. Per-excerpt CER is undefined at denominator0. Include insertions in pooled numerator when other references have characters.',
        'normalization': 'NFKC/casefold; remove whitespace and Unicode punctuation; no number equivalence or semantic rewrite',
        'per_excerpt': rows}


def validate_word_reference(result, manifest, manifest_sha, selection_sha, blind_sha, targets):
    expected = {'schema_version', 'campaign_id', 'manifest_sha256', 'selection_private_sha256',
                'blind_reference_sha256', 'reviewer', 'independence_confirmed',
                'fixed_target_denominator', 'items', 'complete', 'exported_at'}
    require(isinstance(result, dict) and set(result) == expected, 'Unexpected word-reference fields')
    require(result['schema_version'] == 'week7-independent-word-timing-result-v1', 'Wrong word-reference schema')
    require(result['campaign_id'] == manifest['campaign_id'] and result['manifest_sha256'] == manifest_sha
            and result['selection_private_sha256'] == selection_sha
            and result['blind_reference_sha256'] == blind_sha, 'Word reference identity mismatch')
    require(result['complete'] is True and result['independence_confirmed'] is True,
            'Word reference unfinished or independence not declared')
    require(isinstance(result['reviewer'], str) and 0 < len(result['reviewer'].strip()) <= 80
            and timestamp(result['exported_at']), 'Invalid word-reference reviewer/time')
    require(type(result['fixed_target_denominator']) is int and result['fixed_target_denominator'] == 20,
            'Fixed twenty-target denominator required')
    items = result['items']
    require(isinstance(items, list) and len(items) == 20
            and [x.get('target_id') for x in items] == [t['target_id'] for t in targets],
            'Word reference must retain all twenty targets in fixed order')
    for item, target in zip(items, targets):
        require(set(item) == {'target_id', 'excerpt_id', 'original_word_id', 'target_text',
            'reference_status', 'human_start_seconds', 'human_end_seconds', 'uncertainty_seconds', 'note', 'complete'},
            'Unexpected word item fields')
        require(item['excerpt_id'] == target['excerpt_id']
                and item['original_word_id'] == target['original_word_id']
                and item['target_text'] == target['predicted_text'], 'Fixed word target identity differs')
        require(item['complete'] is True and isinstance(item['note'], str) and len(item['note']) <= 1000,
                'Incomplete word item or invalid note')
        status = item['reference_status']
        start, end, uncertainty = (item[k] for k in ('human_start_seconds', 'human_end_seconds', 'uncertainty_seconds'))
        if status == 'locatable':
            require(all(finite_number(x) for x in (start, end, uncertainty))
                    and 0 <= start < end <= 20 and 0 <= uncertainty <= 20,
                    'Invalid manually measured word times/uncertainty')
        else:
            require(status in ('mismatch', 'unlocatable') and start is None and end is None and uncertainty is None,
                    'Mismatch/unlocatable targets must not invent timestamps')
    return result


def word_metrics(items, targets):
    require(len(items) == len(targets) == 20, 'Keep every fixed word target')
    rows = []
    for item, target in zip(items, targets):
        row = {'target_id': target['target_id'], 'excerpt_id': target['excerpt_id'],
               'reference_status': item['reference_status'], 'timing_status': 'not_measurable',
               'start_error': None, 'end_error': None}
        if item['reference_status'] == 'locatable':
            start = endpoint_error(target['predicted_start_excerpt_seconds'], item['human_start_seconds'],
                                   item['uncertainty_seconds'])
            end = endpoint_error(target['predicted_end_excerpt_seconds'], item['human_end_seconds'],
                                 item['uncertainty_seconds'])
            states = (start['status'], end['status'])
            row.update(start_error=start, end_error=end,
                       timing_status='pass' if states == ('pass', 'pass') else
                           'fail' if 'fail' in states else 'uncertain')
        rows.append(row)
    counts = Counter(x['timing_status'] for x in rows)
    states = Counter(x['reference_status'] for x in rows)
    endpoints = [x[k] for x in rows for k in ('start_error', 'end_error') if x[k] is not None]
    return {'fixed_target_denominator': 20, 'threshold_seconds': THRESHOLD,
        'target_reference_status_counts': {k: states[k] for k in ('locatable', 'mismatch', 'unlocatable')},
        'timing_classification_counts': {k: counts[k] for k in ('pass', 'fail', 'uncertain', 'not_measurable')},
        'verified_both_endpoints_within_threshold_count': counts['pass'],
        'verified_within_threshold_rate_fixed_twenty': counts['pass'] / 20,
        'measurable_target_count': states['locatable'], 'measurable_endpoint_count': len(endpoints),
        'measurable_subset_endpoint_mae_seconds': sum(x['absolute_point_error_seconds'] for x in endpoints) / len(endpoints) if endpoints else None,
        'measurable_subset_maximum_absolute_point_error_seconds': max((x['absolute_point_error_seconds'] for x in endpoints), default=None),
        'measurable_subset_maximum_absolute_error_bound_seconds': max((x['maximum_absolute_error_bound_seconds'] for x in endpoints), default=None),
        'mismatch_or_unlocatable_excluded_from_fixed_denominator': False,
        'point_metrics_scope': 'Measured locatable endpoints only; human uncertainty remains in conservative pass/fail/uncertain classification.',
        'new_excerpt_asr_word_times_substituted_for_predeclared_full_asr_targets': False,
        'target_prediction_scope': 'Original predeclared full-source ASR word times translated into fixed20s core timeline; independent new excerpt ASR is used for CER only.',
        'per_target': rows}


def score(validation_root, blind_path=None, word_path=None, campaign_root=None):
    root = Path(validation_root).expanduser().resolve()
    require(root.is_dir() and not root.is_symlink(), 'validation-root must be an existing ordinary packet directory')
    pack = root / 'human-reference-pack'
    manifest_path = pack / 'manifest.public.json'
    manifest = read(manifest_path)
    selection_path = root / 'new-reference-selection.private.json'
    selection = read(selection_path)
    manifest_sha, selection_sha = sha(manifest_path), sha(selection_path)
    require(selection_sha == manifest['selection_private_sha256']
            and selection['new_reference_campaign_id'] == manifest['campaign_id'], 'Frozen selection mismatch')
    targets = [{**target, 'excerpt_id': source['excerpt_id']}
               for source in selection['sources'] for target in source['predeclared_word_targets']]
    require([t['target_id'] for t in targets] == [f'T{i:02}' for i in range(1, 21)], 'Fixed target list changed')
    asr_folder = root / 'new-excerpt-asr'
    asr_summary = read(asr_folder / 'summary.private.json')
    require(asr_summary['campaign_id'] == manifest['campaign_id']
            and asr_summary['manifest_sha256'] == manifest_sha, 'Exact-excerpt ASR identity mismatch')
    require(len(asr_summary['result_rows']) == 3
            and asr_summary['attempted_top_level_transcribe_calls'] == 3
            and asr_summary['runtime'] == {'device': 'cpu', 'compute_type': 'int8', 'cpu_threads': 4, 'num_workers': 1},
            'Keep three actual CPU/base excerpt attempts')
    hypotheses, asr_statuses = {}, []
    for row, media in zip(asr_summary['result_rows'], manifest['transcript_excerpts']):
        require(row['excerpt_id'] == media['excerpt_id'] and row['input_sha256'] == media['sha256'],
                'Machine excerpt ordering/identity differs')
        asr_statuses.append({'excerpt_id': row['excerpt_id'], 'status': row['status']})
        if row['status'] == 'completed':
            file = asr_folder / row['prediction_file']
            require(sha(file) == row['prediction_sha256'], 'Machine prediction bytes changed')
            data = read(file)
            require(data['media']['normalized_sha256'] == media['sha256']
                    and data['media']['total_frames'] == 600 and data['media']['duration_seconds'] == 20
                    and data['media']['fps'] == 30 and data['artifact_kind'] == 'asr_analysis'
                    and data['result_kind'] == 'measured' and data['analyzer']['status'] == 'ok',
                    'Machine output is not the exact twenty-second excerpt')
            require(data['analyzer']['model'] == 'Systran/faster-whisper-base'
                    and all(data['analyzer']['parameters'][k] == v for k, v in asr_summary['runtime'].items()),
                    'Unexpected real machine prediction runtime/model')
            hypotheses[row['excerpt_id']] = data
    public = {'schema_version': 'week7-new-human-accuracy-public-v1',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'client_date': '2026-11-02',
        'campaign_id': manifest['campaign_id'], 'parent_campaign_id': manifest['parent_campaign_id'],
        'status': 'pending_blind_human_reference', 'human_references_fabricated': False,
        'fixed_current_source_denominator': 30, 'new_excerpt_count': 3, 'new_excerpt_audio_seconds': 60,
        'new_whole_shot_source_count': 1, 'fixed_word_timing_target_count': 20,
        'manifest_sha256': manifest_sha, 'selection_private_sha256': selection_sha,
        'machine_exact_excerpt_asr': {'fixed_excerpts': 3, 'completed_excerpts': len(hypotheses),
            'top_level_transcribe_calls': asr_summary['attempted_top_level_transcribe_calls'],
            'model': asr_summary['model_identity']['repository'], 'runtime': asr_summary['runtime'],
            'summary_sha256': sha(asr_folder / 'summary.private.json'), 'statuses': asr_statuses,
            'human_labels_used_for_machine_predictions': False},
        'new_excerpt_asr_cer': None, 'new_whole_source_shot_f1': None,
        'mixed_historical9_plus_new1_shot_f1': None, 'independent_word_timing': None,
        'full_thirty_source_cer': None, 'full_thirty_source_shot_f1': None,
        'limits': ['Three fixed new-source excerpts and one whole new-source cut list are a small descriptive validation sample, not thirty-source gold coverage.',
            'Human independence is self-declared; source distinctness is user-attested rather than externally verified.',
            'Do not tune parameters or change the cohort using these reference outcomes.',
            'Partially unintelligible reference text remains a gap; markers are not CER characters.',
            'No-speech references can reveal insertions; undefined zero-denominator CER is not automatically zero.',
            'Word mismatch/unlocatable entries remain among twenty fixed targets; no timestamps are imputed.',
            'Word timestamp accuracy is distinct from rendered subtitle appearance/speech alignment.',
            'Existing 141-clip quality ratings are preserved and are not replaced or repeated.']}
    private = {'public_summary': public, 'per_word_private': None, 'input_hashes': {},
               'script_sha256': sha(Path(__file__)), 'no_models_or_network_called': True}
    if blind_path is None:
        require(word_path is None, 'Word reference cannot precede a blind reference')
        return private
    blind = validate_blind(read(blind_path), manifest, manifest_sha)
    blind_sha = sha(blind_path)
    private['input_hashes']['blind_reference_sha256'] = blind_sha
    # Verify bytes only on actual scoring; pending-state inspection does no media work.
    for core in manifest['transcript_excerpts']:
        require(sha(pack / core['media']) == core['sha256'], 'Reference media bytes changed')
    full = manifest['full_shot_source']
    require(sha(pack / full['media']) == full['sha256'], 'Whole shot reference media changed')
    public['blind_reference_sha256'] = blind_sha
    public['new_excerpt_asr_cer'] = transcript_metrics(blind['transcript_excerpts'], hypotheses)
    source = selection['sources'][0]
    if campaign_root is not None:
        prediction_path = Path(campaign_root).expanduser().resolve() / 'sources' / source['video_id'] / 'shots.json'
    elif (root / 'S01-shot-prediction.private.json').is_file():
        prediction_path = root / 'S01-shot-prediction.private.json'
    else:
        prediction_path = Path(source['normalized_path']).parent / 'shots.json'
    prediction = read(prediction_path)
    require(prediction['artifact_kind'] == 'shot_analysis' and prediction['result_kind'] == 'measured'
            and prediction['analyzer']['status'] == 'ok'
            and prediction['media']['normalized_sha256'] == full['sha256']
            and prediction['media']['total_frames'] == full['normalized_total_frames']
            and prediction['media']['fps'] == 30, 'New full-source frozen shot prediction mismatch')
    require(prediction['analyzer']['tool'] == 'PySceneDetect.ContentDetector'
            and prediction['analyzer']['parameters']['threshold'] == 27.0
            and prediction['analyzer']['parameters']['min_scene_len_frames'] == 15,
            'Unexpected frozen default detector')
    cuts = blind['shot_reference']['cut_frames']
    matched = cut_match(cuts, prediction['cut_frames'], full['normalized_total_frames'], 3)
    public['new_whole_source_shot_f1'] = {'scope': 'One fixed newly-added source; new human cut list, original frozen default prediction',
        'fixed_source_denominator': 1, 'duration_seconds': full['duration_seconds'],
        'reference_cuts': len(cuts), 'prediction_cuts': len(prediction['cut_frames']),
        'tolerance_frames': 3, 'tolerance_seconds': .1, **combine([matched]),
        'correct_no_cuts': not cuts and not prediction['cut_frames'],
        'prediction_sha256': sha(prediction_path), 'prediction_analyzer': prediction['analyzer'],
        'independent_double_frame_annotation': False}
    prior_path = root / 'rescored-existing-refs-v2/accuracy-summary.public.json'
    prior = read(prior_path)['current_frozen_shot_accuracy']
    require(prior['reference_source_count'] == 9 and prior['all_nine_sources_are_development_known'] is True,
            'Expected existing nine-source development diagnostic')
    public['mixed_historical9_plus_new1_shot_f1'] = {'scope': 'Descriptive mixed-exposure10 sources; preserve old9/new1 separate strata',
        'historical_development_source_count': 9, 'newly_added_source_count': 1,
        'historical_development_micro': prior['micro'],
        'new_source_micro': combine([matched]), 'mixed_micro': combine([prior['micro'], matched]),
        'is_full_thirty_source_accuracy': False}
    public['status'] = 'pending_word_timing_reference'
    if word_path is None:
        return private
    release_path = root / 'word-timing-release.private.json'
    release = read(release_path)
    require(release['blind_reference_sha256'] == blind_sha
            and release['manifest_sha256'] == manifest_sha
            and release['selection_private_sha256'] == selection_sha and release['target_count'] == 20,
            'Actual gated word-target release receipt required')
    require(release['predicted_timestamps_disclosed'] is False
            and release['target_filtering_after_reference'] is False, 'Invalid target-release provenance')
    word = validate_word_reference(read(word_path), manifest, manifest_sha, selection_sha, blind_sha, targets)
    metrics = word_metrics(word['items'], targets)
    private['per_word_private'] = metrics.pop('per_target')
    public['independent_word_timing'] = metrics
    private['input_hashes']['word_reference_sha256'] = sha(word_path)
    private['input_hashes']['word_release_receipt_sha256'] = sha(release_path)
    public['word_reference_sha256'] = sha(word_path)
    public['status'] = 'completed_fixed_sample_evaluation' if public['new_excerpt_asr_cer']['all_three_eligible'] else 'completed_with_partial_transcript_reference_limitations'
    require(sha(blind_path) == blind_sha, 'Blind reference changed during scoring')
    return private


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validation-root', type=Path, required=True, help='Local accuracy packet root; not included in this repository')
    parser.add_argument('--campaign-root', type=Path, help='Relocated frozen full campaign root for source shot predictions')
    parser.add_argument('--blind-reference', type=Path)
    parser.add_argument('--word-reference', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Use a new output path; preserve existing reports')
    try:
        report = score(args.validation_root, args.blind_reference, args.word_reference, args.campaign_root)
    except (ValueError, TypeError, KeyError, OSError) as error:
        report = {'public_summary': {'status': 'blocked_invalid_reference_input',
            'new_excerpt_asr_cer': None, 'new_whole_source_shot_f1': None, 'independent_word_timing': None},
            'error_type': type(error).__name__, 'error': str(error),
            'no_human_labels_invented': True}
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': report['public_summary']['status'], 'output': str(args.output)}, ensure_ascii=True))
    return 2 if report['public_summary']['status'] == 'blocked_invalid_reference_input' else 0


if __name__ == '__main__':
    raise SystemExit(main())
