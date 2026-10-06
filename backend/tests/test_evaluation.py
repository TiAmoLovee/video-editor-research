from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from clipforge.decision.candidates import generate_candidates, content_hash
from clipforge.decision.evaluation import freeze, summary, QUESTIONS, validate_answers
from clipforge.decision.evaluation_server import create_app
from clipforge.decision.reviewed import file_hash
from clipforge.decision.scoring import score_candidates
from clipforge.decision.selection import build_selection
from backend.tests.test_candidates import fixture


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.analysis = fixture([(i*100+.12, i*100+20.18) for i in range(10)])
        self.scored = score_candidates(generate_candidates(self.analysis), self.analysis)
        self.manifest = freeze(self.analysis, self.scored, 'test.mp4')

    def test_frozen_cohort_contains_every_nms_survivor_and_preserves_sources(self):
        before = content_hash(self.scored)
        result = build_selection(self.scored, self.analysis)
        self.assertEqual({i['candidate_id'] for i in result['items'] if i['retained']},
                         {i['candidate_id'] for i in self.manifest['samples']})
        self.assertEqual(self.manifest, freeze(self.analysis, self.scored, 'test.mp4'))
        self.assertEqual(self.manifest['denominator'], 10)
        self.assertEqual(self.manifest['required_passes'], 9)
        self.assertEqual(before, content_hash(self.scored))
        for item in self.manifest['samples']:
            self.assertLessEqual(item['start_frame']/30, item['original_start'])
            self.assertLess(item['original_start']-item['start_frame']/30, 1/30)
            self.assertGreaterEqual(item['end_frame_exclusive']/30, item['original_end'])
            self.assertLess(item['end_frame_exclusive']/30-item['original_end'], 1/30)

    def test_pending_is_not_zero_or_a_pass_and_uncertainty_keeps_fixed_denominator(self):
        reviews = {}
        self.assertIsNone(summary(self.manifest, reviews)['boundary_rate'])
        for item in self.manifest['samples']:
            reviews[item['id']] = {'answers': dict.fromkeys(QUESTIONS, 'yes'), 'note': ''}
        reviews['S01']['answers']['ending_complete'] = 'uncertain'
        reviews['S02']['answers']['understandable'] = 'no'
        result = summary(self.manifest, reviews)
        self.assertEqual(result['boundary_rate'], .9)
        self.assertEqual(result['usable_rate'], .8)
        self.assertTrue(result['pilot_meets_85_percent'])
        self.assertEqual(result['overall_week4_acceptance'], 'not_established')
        reviews['S03']['answers']['opening_complete'] = 'no'
        self.assertFalse(summary(self.manifest, reviews)['pilot_meets_85_percent'])
        del reviews['S04']
        self.assertIsNone(summary(self.manifest, reviews)['boundary_rate'])

    def test_boolean_missing_and_unknown_answers_are_rejected(self):
        for answers in ({}, dict.fromkeys(QUESTIONS, True), dict.fromkeys(QUESTIONS, 'pass')):
            with self.assertRaises(ValueError):
                validate_answers(answers, '')
        with self.assertRaises(ValueError):
            summary(self.manifest, {'unknown': {'answers': dict.fromkeys(QUESTIONS, 'yes'), 'note': ''}})

    def create_batch(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        manifest = deepcopy(self.manifest)
        manifest['renders'] = {}
        for item in manifest['samples']:
            # Synthetic bytes only for HTTP persistence/security tests; never used as audition evidence.
            path = root/item['file']; path.write_bytes(item['id'].encode()*100)
            manifest['renders'][item['id']] = {'file': item['file'], 'sha256': file_hash(path)}
        (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        client = TestClient(create_app(root), base_url='http://127.0.0.1:8307')
        self.addCleanup(client.close)
        return root, client

    def test_user_reviews_persist_with_history_and_conflicting_tab_cannot_overwrite(self):
        root, client = self.create_batch()
        data = {'batch_id': self.manifest['batch_id'], 'revision': 0,
                'answers': dict.fromkeys(QUESTIONS, 'yes'), 'note': 'synthetic test only'}
        headers = {'Origin': 'http://127.0.0.1:8307'}
        response = client.post('/api/review/S01', json=data, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['pending'], 9)
        first = (root/'reviews/review-000001.json').read_bytes()
        self.assertEqual(client.post('/api/review/S01', json=data, headers=headers).status_code, 409)
        data['revision'] = 1; data['answers']['ending_complete'] = 'no'
        self.assertEqual(client.post('/api/review/S01', json=data, headers=headers).status_code, 200)
        self.assertEqual(first, (root/'reviews/review-000001.json').read_bytes())
        with TestClient(create_app(root), base_url='http://127.0.0.1:8307') as restarted:
            saved = restarted.get('/api/reviews').json()
            self.assertEqual(saved['revision'], 2)
            self.assertEqual(saved['reviews']['S01']['answers']['ending_complete'], 'no')
            self.assertEqual(restarted.get('/api/export').json()['human_review'], saved)

    def test_other_origins_wrong_batch_and_invalid_media_cannot_change_records(self):
        root, client = self.create_batch()
        payload = {'batch_id': self.manifest['batch_id'], 'revision': 0,
                   'answers': dict.fromkeys(QUESTIONS, 'yes'), 'note': ''}
        self.assertEqual(client.post('/api/review/S01', json=payload).status_code, 403)
        self.assertEqual(client.post('/api/review/S01', json=payload, headers={'Origin': 'https://example.com'}).status_code, 403)
        headers = {'Origin': 'http://127.0.0.1:8307'}
        self.assertEqual(client.post('/api/review/unknown', json=payload, headers=headers).status_code, 422)
        payload['batch_id'] = 'other'
        self.assertEqual(client.post('/api/review/S01', json=payload, headers=headers).status_code, 422)
        self.assertEqual(client.get('/media/unknown').status_code, 404)
        self.assertEqual(client.get('/media/S01', headers={'Range': 'bytes=0-9'}).status_code, 206)
        (root/'S01.mp4').write_bytes(b'changed')
        self.assertEqual(client.get('/media/S01').status_code, 409)
        self.assertEqual(client.get('/api/reviews').json()['revision'], 0)

    def test_corrupt_manifest_or_feedback_fails_explicitly_and_scores_are_hidden(self):
        root, client = self.create_batch()
        sample = client.get('/api/batch').json()['samples'][0]
        self.assertNotIn('original_rank', sample)
        self.assertNotIn('original_score', sample)
        (root/'reviews/review-000001.json').write_text('{}', encoding='utf-8')
        with self.assertRaises((ValueError, KeyError)):
            create_app(root)
        data = json.loads((root/'manifest.json').read_text())
        data['samples'][0]['end_frame_exclusive'] += 1
        (root/'manifest.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            create_app(root)


if __name__ == '__main__':
    unittest.main()
