"""Pure character, cut-boundary and timing-support metrics; no data access."""
from collections import Counter
import unicodedata


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cut_match(reference, prediction, total, tolerance=3):
    for values in (reference, prediction):
        require(isinstance(values, list) and values == sorted(set(values))
                and all(type(x) is int and 0 < x < total for x in values),
                'Invalid internal cut list')
    i = j = 0
    matches = []
    while i < len(reference) and j < len(prediction):
        r, p = reference[i], prediction[j]
        if p < r - tolerance:
            j += 1
        elif r < p - tolerance:
            i += 1
        else:
            matches.append({'reference_frame': r, 'prediction_frame': p,
                            'offset_frames': p - r})
            i += 1
            j += 1
    return {'tp': len(matches), 'fp': len(prediction) - len(matches),
            'fn': len(reference) - len(matches), 'matches': matches}


def combine(scores):
    sums = {k: sum(x[k] for x in scores) for k in ('tp', 'fp', 'fn')}
    tp, fp, fn = (sums[k] for k in ('tp', 'fp', 'fn'))
    return {**sums, 'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def normalize_text(text):
    return ''.join(ch for ch in unicodedata.normalize('NFKC', text).casefold()
                   if not ch.isspace() and not unicodedata.category(ch).startswith('P'))


def character_errors(reference, hypothesis):
    # Deterministic minimal Levenshtein alignment; diagonal, deletion, insertion
    # is the tie policy. Individual S/D/I values may depend on tie alignment.
    rows = [[j for j in range(len(hypothesis) + 1)]]
    for i, r in enumerate(reference, 1):
        row = [i]
        for j, p in enumerate(hypothesis, 1):
            row.append(min(rows[-1][j] + 1, row[-1] + 1,
                           rows[-1][j - 1] + (r != p)))
        rows.append(row)
    i, j = len(reference), len(hypothesis)
    counts = Counter()
    while i or j:
        if i and j and rows[i][j] == rows[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            counts['substitutions' if reference[i - 1] != hypothesis[j - 1] else 'correct'] += 1
            i -= 1
            j -= 1
        elif i and rows[i][j] == rows[i - 1][j] + 1:
            counts['deletions'] += 1
            i -= 1
        else:
            require(j > 0, 'Invalid edit alignment')
            counts['insertions'] += 1
            j -= 1
    count = rows[-1][-1]
    require(count == sum(counts[k] for k in ('substitutions', 'deletions', 'insertions')),
            'Inconsistent edit count')
    return {'reference_characters': len(reference), 'hypothesis_characters': len(hypothesis),
            'edit_distance': count,
            **{k: counts[k] for k in ('substitutions', 'deletions', 'insertions', 'correct')},
            'cer': count / len(reference) if reference else None}
