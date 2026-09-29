from copy import deepcopy
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from clipforge.analysis.shots import frames_to_shots
from clipforge.evaluation.shots import evaluate, match_cuts, score_dataset, write_new


def fixtures(cuts=None, reference=None, identity=1):
    cuts = [30, 60] if cuts is None else cuts
    reference = [30, 60] if reference is None else reference
    media = {"normalized_sha256": f"{identity:064x}", "time_reference": "normalized_video",
             "fps": 30, "total_frames": 90, "duration_seconds": 3.0}
    bounds = [0, *cuts, 90]
    prediction = {"artifact_kind": "shot_analysis", "result_kind": "measured", "schema_version": "0.1.0",
                  "analyzer": {"status": "ok", "parameters": {"threshold": 27}}, "media": media,
                  "cut_frames": cuts, "shots": frames_to_shots(list(zip(bounds[:-1], bounds[1:])), 90)}
    annotation = {"format_version": "1", "annotation_kind": "human_shot_boundaries",
                  "source_sha256": f"{identity + 100:064x}", "media": deepcopy(media),
                  "status": "verified", "reviewer": "synthetic unit-test fixture (not human evidence)",
                  "reviewed_at": "2026-09-28", "cut_frames": reference}
    return annotation, prediction


class ShotEvaluationTests(unittest.TestCase):
    def test_tolerance_is_inclusive_and_extra_predictions_count_as_fp(self):
        score = match_cuts([30, 60], [27, 32, 64], 90, 3)
        self.assertEqual((score['tp'], score['fp'], score['fn']), (1, 2, 1))
        self.assertEqual(score['missed_frames'], [60])
        self.assertEqual(score['extra_frames'], [32, 64])
        self.assertAlmostEqual(score['f1'], 0.4)

    def test_earliest_feasible_matching_does_not_lose_available_pairs(self):
        score = match_cuts([10, 14], [7, 11], 20, 3)
        self.assertEqual(score['tp'], 2)

    def test_matching_count_agrees_with_small_exhaustive_optimum(self):
        # 对所有短序列用穷举匹配交叉检查，不仅复制实现的贪心判断。
        subsets = [list(xs) for n in range(4) for xs in itertools.combinations(range(1, 6), n)]
        for reference in subsets:
            for predicted in subsets:
                optimum = 0
                for size in range(1, min(len(reference), len(predicted)) + 1):
                    feasible = any(all(abs(r - p) <= 1 for r, p in zip(rs, ps))
                                   for rs in itertools.combinations(reference, size)
                                   for ps in itertools.permutations(predicted, size))
                    if feasible:
                        optimum = size
                self.assertEqual(match_cuts(reference, predicted, 10, 1)['tp'], optimum)

    def test_no_cuts_is_not_fabricated_perfect_f1(self):
        result = match_cuts([], [], 90)
        self.assertIsNone(result['f1'])
        self.assertTrue(result['correct_no_cuts'])
        self.assertEqual(match_cuts([], [30], 90)['f1'], 0)
        self.assertEqual(match_cuts([30], [], 90)['f1'], 0)

    def test_rejects_bad_cut_lists_and_tolerance(self):
        for values in (None, [0], [90], [True], [1.5], [30, 30], [60, 30]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                match_cuts(values, [], 90)
        for tolerance in (-1, True, float('nan'), 1.5):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                match_cuts([], [], 90, tolerance)

    def test_draft_and_missing_review_metadata_cannot_be_scored(self):
        for key, value in [('status', 'draft'), ('reviewer', ''), ('reviewed_at', ''), ('cut_frames', None)]:
            annotation, prediction = fixtures()
            annotation[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                evaluate(annotation, prediction)

    def test_media_mismatch_and_invalid_partition_are_rejected(self):
        annotation, prediction = fixtures()
        annotation['media']['normalized_sha256'] = 'a' * 64
        with self.assertRaises(ValueError):
            evaluate(annotation, prediction)
        annotation, prediction = fixtures()
        prediction['shots'][0]['end'] = 0.5
        with self.assertRaises(ValueError):
            evaluate(annotation, prediction)

    def test_exact_score_is_separate_and_inputs_are_unchanged(self):
        annotation, prediction = fixtures(cuts=[31, 61])
        before = deepcopy((annotation, prediction))
        score = evaluate(annotation, prediction)
        self.assertEqual(score['primary']['f1'], 1)
        self.assertEqual(score['exact']['f1'], 0)
        self.assertEqual((annotation, prediction), before)

    def make_dataset(self, folder, count=10):
        videos = []
        for i in range(count):
            annotation, prediction = fixtures(identity=i + 1)
            write_new(folder / f'a{i}.json', annotation)
            write_new(folder / f'p{i}.json', prediction)
            videos.append({'id': f'v{i}', 'kind': 'real', 'split': 'evaluation',
                           'category': ('interview', 'course', 'vlog')[i % 3],
                           'source_sha256': annotation['source_sha256'],
                           'annotation': f'a{i}.json', 'prediction': f'p{i}.json'})
        manifest = {'format_version': '1', 'tolerance_frames': 3, 'videos': videos}
        self.save(folder / 'manifest.json', manifest)
        return manifest

    def save(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_dataset_requires_ten_videos_and_class_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = self.make_dataset(folder)
            full = score_dataset(folder / 'manifest.json')
            self.assertTrue(full['shot_f1_target_met'])
            self.assertFalse(full['week3_complete'])
            manifest['videos'].pop()
            self.save(folder / 'manifest.json', manifest)
            short = score_dataset(folder / 'manifest.json')
            self.assertEqual(short['overall']['f1'], 1)
            self.assertFalse(short['shot_f1_target_met'])
            for item in manifest['videos']:
                item['category'] = 'interview'
            self.save(folder / 'manifest.json', manifest)
            self.assertFalse(score_dataset(folder / 'manifest.json')['dataset_ready'])

    def test_duplicates_mixed_detectors_and_wrong_source_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = self.make_dataset(folder, 2)
            original = deepcopy(manifest)
            manifest['videos'][1]['source_sha256'] = manifest['videos'][0]['source_sha256']
            self.save(folder / 'manifest.json', manifest)
            with self.assertRaises(ValueError):
                score_dataset(folder / 'manifest.json')
            self.save(folder / 'manifest.json', original)
            prediction = json.loads((folder / 'p1.json').read_text())
            prediction['analyzer']['parameters']['threshold'] = 20
            self.save(folder / 'p1.json', prediction)
            with self.assertRaises(ValueError):
                score_dataset(folder / 'manifest.json')
            original['videos'][0]['source_sha256'] = 'f' * 64
            self.save(folder / 'manifest.json', original)
            with self.assertRaises(ValueError):
                score_dataset(folder / 'manifest.json')

    def test_pending_labels_are_reported_without_inventing_scores(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = self.make_dataset(folder, 3)
            manifest['videos'][0]['category'] = 'unknown'
            manifest['videos'][1]['annotation'] = 'absent.json'
            self.save(folder / 'manifest.json', manifest)
            annotation = json.loads((folder / 'a2.json').read_text())
            annotation['status'] = 'draft'
            self.save(folder / 'a2.json', annotation)
            result = score_dataset(folder / 'manifest.json')
            self.assertEqual(len(result['pending']), 3)
            self.assertEqual(result['scored_videos'], 0)
            self.assertIsNone(result['overall']['f1'])

    def test_normalized_duplicates_and_insufficient_category_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            manifest = self.make_dataset(folder)
            for item in manifest['videos']:
                item['category'] = 'interview'
            self.save(folder / 'manifest.json', manifest)
            self.assertFalse(score_dataset(folder / 'manifest.json')['dataset_ready'])
            annotation = json.loads((folder / 'a1.json').read_text())
            prediction = json.loads((folder / 'p1.json').read_text())
            annotation['media']['normalized_sha256'] = f'{1:064x}'
            prediction['media']['normalized_sha256'] = f'{1:064x}'
            self.save(folder / 'a1.json', annotation)
            self.save(folder / 'p1.json', prediction)
            with self.assertRaises(ValueError):
                score_dataset(folder / 'manifest.json')

    def test_write_new_preserves_existing_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'annotation.json'
            write_new(path, {'cut_frames': [30]})
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_new(path, {'cut_frames': []})
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
