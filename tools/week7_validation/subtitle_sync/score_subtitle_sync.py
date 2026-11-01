"""Compare explicit new human speech ranges with independently decoded caption transitions.

No ASR inference, guessed speech timestamps, answer edits, or new media rendering.
Output is private because it contains the observer and per-cue measurements.
"""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    require(path.stat().st_size <= 2 * 1024 * 1024, 'Input is too large')
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Non-finite JSON constant')
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def numeric(value):
    return type(value) in (float, int) and math.isfinite(value)


def classify(lower, upper):
    tolerance = Fraction(3, 10)
    if lower >= -tolerance and upper <= tolerance:
        return 'within_tolerance'
    if upper < -tolerance or lower > tolerance:
        return 'outside_tolerance'
    return 'uncertain'


def score(root, observations_path):
    manifest_path = root / 'manifest.private.json'
    manifest = read(manifest_path)
    captions = read(root / 'caption-boundaries.private.json')
    before = sha(observations_path)
    observations = read(observations_path)
    require(set(observations) == {'schema_version', 'protocol_id', 'manifest_sha256', 'observer',
        'independent_audio_annotation_confirmed', 'exported_at', 'targets'}, 'Unexpected observation fields')
    require(observations['schema_version'] == 'week7-subtitle-speech-observations-v1'
        and observations['protocol_id'] == manifest['protocol_id']
        and observations['manifest_sha256'] == sha(manifest_path), 'Wrong measurement campaign/manifest')
    require(isinstance(observations['observer'], str) and observations['observer'].strip()
        and observations['independent_audio_annotation_confirmed'] is True, 'Observer and independent-audio confirmation required')
    try:
        require(datetime.fromisoformat(observations['exported_at'].replace('Z', '+00:00')).utcoffset() is not None,
            'Timezone-aware observation time required')
    except (AttributeError, TypeError):
        raise ValueError('Invalid observation time') from None
    require(captions['mp4_sha256'] == manifest['render']['sha256'], 'Caption/render fingerprint mismatch')
    require(manifest['fixed_target_cue_count'] == 10 and manifest['expected_speech_endpoint_count'] == 20,
        'Require the fixed ten-cue measurement')
    expected = {item['target_id']: item for item in manifest['targets']}
    boundaries = {item['cue_id']: item for item in captions['targets']}
    require(len(expected) == len(boundaries) == 10 and isinstance(observations['targets'], list)
        and len(observations['targets']) == 10, 'Missing or duplicate targets')
    seen = set()
    measured = []
    for row in observations['targets']:
        require(isinstance(row, dict) and set(row) == {'target_id', 'audio_sha256', 'speech_start', 'speech_end'},
            'Unexpected target fields')
        tid = row['target_id']
        require(tid in expected and tid not in seen, 'Wrong or duplicate target')
        seen.add(tid)
        reference = expected[tid]
        require(row['audio_sha256'] == reference['audio_sha256']
            and sha(root / reference['audio']) == reference['audio_sha256'], 'Listening audio fingerprint mismatch')
        boundary = boundaries[reference['cue_id']]
        # Every excerpt uses a 48 kHz PCM sample clock and integer-frame 30 fps EDL offsets.
        offset_value = reference['audio_window_to_output_offset_seconds']
        require(numeric(offset_value), 'Invalid audio mapping')
        offset = Fraction(round(offset_value * 48000), 48000)
        require(abs(float(offset) - offset_value) < 1e-10, 'Audio mapping is not on the 48 kHz sample clock')
        endpoints = {}
        for field, caption_field, edge_field in (('speech_start', 'start_interval_exact', 'start'),
                ('speech_end', 'end_interval_exact', 'end')):
            value = row[field]
            require(isinstance(value, dict) and set(value) == {'status', 'earliest_seconds', 'latest_seconds'},
                'Unexpected endpoint fields')
            status = value['status']
            require(status in ('measured', 'unlocatable', 'cropped'), 'Every endpoint must be explicitly resolved')
            result = {'human_status': status, 'caption_file_edge': boundary['file_edge_endpoints'][edge_field]}
            if status != 'measured':
                require(value['earliest_seconds'] is None and value['latest_seconds'] is None,
                    'Unlocated endpoints must not carry invented times')
                result.update(classification='not_measured', delta_interval_exact=None)
            else:
                lo, hi = value['earliest_seconds'], value['latest_seconds']
                require(numeric(lo) and numeric(hi) and 0 <= lo <= hi <= reference['audio_duration_seconds'] + .001,
                    'Invalid human speech range')
                lower, upper = offset + Fraction(str(lo)), offset + Fraction(str(hi))
                result['human_output_interval_exact'] = [str(lower), str(upper)]
                if result['caption_file_edge']:
                    # The endpoint is retained; no adjacent outside-file frame was observed.
                    result.update(classification='file_edge_transition_not_bracketed', delta_interval_exact=None)
                else:
                    start, end = map(Fraction, boundary[caption_field])
                    delta = [start - upper, end - lower]
                    result.update(classification=classify(*delta), delta_interval_exact=list(map(str, delta)),
                        delta_interval_seconds=list(map(float, delta)),
                        maximum_absolute_error_bound_seconds=float(max(map(abs, delta))))
            endpoints[field] = result
        a, b = row['speech_start'], row['speech_end']
        if a['status'] == b['status'] == 'measured':
            require(a['earliest_seconds'] <= b['latest_seconds'], 'Speech start is after speech end')
        classifications = [value['classification'] for value in endpoints.values()]
        cue_status = ('outside_tolerance' if 'outside_tolerance' in classifications else
            'within_tolerance' if all(value == 'within_tolerance' for value in classifications) else
            'uncertain' if 'uncertain' in classifications else 'not_fully_measurable')
        measured.append({'target_id': tid, 'cue_id': reference['cue_id'], 'cue_status': cue_status, 'endpoints': endpoints})
    require(sha(observations_path) == before, 'Observation file changed while being read')
    counts = Counter(endpoint['classification'] for row in measured for endpoint in row['endpoints'].values())
    return {'schema_version': 'week7-subtitle-independent-timing-result-v1',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'human_ranges_compared_with_explicit_limits',
        'protocol_id': manifest['protocol_id'], 'observer': observations['observer'],
        'manifest_sha256': sha(manifest_path), 'observations_sha256': before,
        'caption_evidence_sha256': sha(root / 'caption-boundaries.private.json'),
        'scoring_tool_sha256': sha(Path(__file__)), 'fixed_target_cue_count': 10, 'fixed_endpoint_count': 20,
        'threshold_exact_seconds': '3/10', 'endpoint_classification_counts': dict(counts),
        'cue_classification_counts': dict(Counter(row['cue_status'] for row in measured)), 'targets': measured,
        'limits': ['One source and one previously calibrated render; only the ten previously unmeasured cues are new.',
            'Two file-edge caption transitions lack adjacent outside-file frames and are retained separately, never automatic passes.',
            'Untrimmed original-source audio provides extra context for the two edge cues; output mapping may be negative or past EOF.',
            'No three old calibration-cue measurements are pooled as new independent evidence.',
            'Human uncertainty ranges are observational brackets, not statistical confidence intervals.',
            'Missing, cropped, unlocatable and wide uncertain endpoints never become fabricated yes or no answers.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.output.exists(), 'Use a new output file; existing measurements are never overwritten')
        result = score(args.root, args.observations)
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(2, f'NO RESULT CREATED: {type(error).__name__}: {error}\n')
    print(json.dumps({'status': result['status'], 'endpoint_classification_counts': result['endpoint_classification_counts']}))


if __name__ == '__main__':
    main()
