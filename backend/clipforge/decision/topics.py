"""Deterministic lexical grouping of time-NMS survivors; never deletes or rescores."""

from collections import Counter
from difflib import SequenceMatcher
import hashlib
import math
import re
import unicodedata

VERSION = 'lexical-topics-v1'
TOPIC_THRESHOLD = 0.28
DUPLICATE_THRESHOLD = 0.92
STOP = {'我们', '你们', '他们', '这个', '那个', '就是', '一个', '那么', '然后', '这样',
        '这种', '什么', '现在', '已经', '时候', '因为', '所以', '还有', 'the', 'and', 'of', 'to', 'a', 'is'}


def normalized(text):
    return unicodedata.normalize('NFKC', text).casefold()


def features(text):
    parts = re.findall(r'[\u4e00-\u9fff]+|[a-z]+|\d+(?:\.\d+)?', normalized(text))
    terms = []
    for part in parts:
        if '\u4e00' <= part[0] <= '\u9fff':
            terms.extend(part[i:i+n] for n in (2, 3) for i in range(len(part)-n+1))
        else:
            terms.append(part)
    return Counter(t for t in terms if t not in STOP)


def compact(text):
    return ''.join(re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+', normalized(text)))


def safeguards(text):
    # Conservative: changes in numeric values or negation must not disappear in folding.
    return Counter(re.findall(r'\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两]+|[不没无未非]|\b(?:not|no|never|cannot)\b|n.t\b', normalized(text)))


def near_duplicate(a, b, similarity):
    left, right = compact(a), compact(b)
    return (similarity >= DUPLICATE_THRESHOLD and min(len(left), len(right)) >= 20
            and min(len(left), len(right)) / max(len(left), len(right)) >= .9
            and safeguards(a) == safeguards(b)
            and SequenceMatcher(None, left, right, autojunk=False).ratio() >= DUPLICATE_THRESHOLD)


def group_topics(candidates):
    """Input: validated NMS survivors. Group deterministically in original score order."""
    ordered = sorted(candidates, key=lambda c: (c['rank'], c['id']))
    counts = {c['id']: features(c['text']) for c in ordered}
    df = Counter(term for values in counts.values() for term in values)
    vectors = {}
    for cid, values in counts.items():
        weighted = {term: (1 + math.log(n)) * (1 + math.log((1 + len(ordered))/(1 + df[term])))
                    for term, n in values.items()}
        norm = math.sqrt(sum(v*v for v in weighted.values()))
        vectors[cid] = {t: v/norm for t, v in weighted.items()} if norm else {}

    def similarity(a, b):
        va, vb = vectors[a['id']], vectors[b['id']]
        if len(va) > len(vb):
            va, vb = vb, va
        return min(1.0, sum(v * vb.get(t, 0) for t, v in va.items()))

    clusters, notes = [], {}
    for candidate in ordered:
        # Complete-link prevents A~B~C chains from folding unrelated A and C.
        cluster = next((g for g in clusters if all(
            near_duplicate(candidate['text'], c['text'], similarity(candidate, c)) for c in g)), None)
        if cluster is None:
            clusters.append([candidate])
            notes[candidate['id']] = {'duplicate_of': None, 'duplicate_rank': None, 'duplicate_similarity': None}
        else:
            representative = cluster[0]
            cluster.append(candidate)
            notes[candidate['id']] = {
                'duplicate_of': representative['id'], 'duplicate_rank': representative['rank'],
                'duplicate_similarity': round(similarity(candidate, representative), 6)}

    groups = []
    for cluster in clusters:
        choices = []
        for index, group in enumerate(groups):
            minimum = min(similarity(a, b) for a in cluster for other in group for b in other)
            if minimum >= TOPIC_THRESHOLD:
                choices.append((minimum, -index, group))
        if choices:
            max(choices, key=lambda choice: choice[:2])[2].append(cluster)
        else:
            groups.append([cluster])

    exported = []
    for group in groups:
        members = sorted([c for cluster in group for c in cluster], key=lambda c: (c['rank'], c['id']))
        recommended = [cluster[0]['id'] for cluster in group]
        gid = 'topic_' + hashlib.sha256('\n'.join(sorted(c['id'] for c in members)).encode()).hexdigest()[:16]
        # Labels are verbatim shared lexical fragments, not model-written topic claims.
        shared = set.intersection(*(set(counts[c['id']]) for c in members))
        weights = {t: sum(vectors[c['id']][t] for c in members) for t in shared}
        keywords = []
        for term in sorted(shared, key=lambda t: (-weights[t], -len(t), t)):
            if any(term in old or old in term for old in keywords):
                continue
            keywords.append(term)
            if len(keywords) == 3:
                break
        # Preserve original spelling (e.g. AI) when the normalized term occurs verbatim.
        keywords = [next((m.group() for c in members
                          if (m := re.search(re.escape(t), c['text'], re.IGNORECASE))), t) for t in keywords]
        label = ' · '.join(keywords) or f"候选 #{members[0]['rank']}"
        exported.append({'id': gid, 'label': label, 'keywords': keywords,
                         'member_ids': [c['id'] for c in members], 'recommended_ids': recommended,
                         'member_count': len(members), 'recommended_count': len(recommended)})
        for c in members:
            notes[c['id']].update(group_id=gid, group_label=label)
    return {'version': VERSION, 'method': 'TF-IDF cosine; Chinese 2/3-grams; complete-link',
            'scope': 'time-NMS survivors only; lexical similarity is not semantic equivalence',
            'config': {'topic_threshold': TOPIC_THRESHOLD, 'duplicate_threshold': DUPLICATE_THRESHOLD},
            'summary': {'input_count': len(ordered), 'group_count': len(groups),
                        'duplicate_count': len(ordered)-len(clusters), 'recommended_count': len(clusters)},
            'groups': exported, 'items': notes, 'model_requests': 0}
