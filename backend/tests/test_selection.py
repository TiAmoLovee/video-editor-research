from copy import deepcopy
import json
import unittest

from clipforge.decision.candidates import generate_candidates, content_hash
from clipforge.decision.scoring import score_candidates
from clipforge.decision.selection import build_selection, temporal_iou
from backend.tests.test_candidates import fixture
from backend.tests.test_candidate_api import CandidateApiTests
from clipforge.storage.jobs import get_job, job_dir, database


def reviewed(data, frame=535):
    return {'version': 'human-scene-review-v1',
            'source_sha256': data['media']['source_sha256'],
            'normalized_sha256': data['media']['normalized_sha256'],
            'cuts': [{'frame': frame, 'fps': 30, 'topic_change': True, 'note': '人工确认话题切换'}]}


def sample():
    data = fixture([(1.43, 11.01), (11.44, 19.74), (21.08, 40)])
    data['words'][1:2] = [
        {'id': 'wa', 'start': 11.44, 'end': 17.22, 'text': '面对这种局', 'probability': .9},
        {'id': 'wb', 'start': 17.72, 'end': 17.90, 'text': '面', 'probability': .13},
        {'id': 'wc', 'start': 17.90, 'end': 19.74, 'text': '签着了我跟你说', 'probability': .9},
    ]
    data['sentences'][1].update(word_ids=['wa', 'wb', 'wc'], text='面对这种局面签着了我跟你说')
    return data, score_candidates(generate_candidates(data), data)


class SelectionTests(unittest.TestCase):
    def test_visual_word_conflict_is_reviewed_and_never_inherits_score(self):
        data, candidates = sample()
        before = content_hash(candidates)
        report = build_selection(candidates, data, reviewed(data))
        raw = next(c for c in candidates['candidates'] if c['start'] == 1.43 and c['end'] == 19.74)
        item = next(i for i in report['items'] if i['candidate_id'] == raw['id'])
        self.assertIn('word_crosses_visual_cut', item['issues'])
        self.assertEqual(item['boundary_status'], 'review_required')
        self.assertEqual(item['proposal']['end'], 17.90)
        self.assertEqual(item['proposal']['source_words'], ['w0', 'wa', 'wb'])
        self.assertNotIn('签着了', item['proposal']['text'])
        self.assertIsNone(item['proposal']['score'])
        self.assertFalse(item['proposal']['semantic_completeness_verified'])
        self.assertEqual(before, content_hash(candidates))
        self.assertEqual(report['model_requests'], 0)

    def test_large_alignment_conflict_produces_no_fake_boundary(self):
        data, _ = sample()
        data['words'][2]['end'] = 18.3
        data['words'][3]['start'] = 18.3
        result = build_selection(score_candidates(generate_candidates(data), data), data, reviewed(data))
        item = next(i for i in result['items'] if 'no_nearby_word_safe_boundary' in i['issues'])
        self.assertIsNone(item['proposal'])

    def test_wrong_media_duplicate_invalid_and_out_of_range_reviews_fail(self):
        data, candidates = sample()
        edits = [lambda r: r.update(source_sha256='f'*64),
                 lambda r: r.update(normalized_sha256='f'*64),
                 lambda r: r['cuts'].append(deepcopy(r['cuts'][0])),
                 lambda r: r['cuts'][0].update(frame=5000),
                 lambda r: r['cuts'][0].update(frame=True),
                 lambda r: r['cuts'][0].update(topic_change='yes'),
                 lambda r: r['cuts'][0].update(fps=25)]
        for edit in edits:
            value = reviewed(data); edit(value)
            with self.assertRaises(ValueError):
                build_selection(candidates, data, value)

    def test_no_review_does_not_invent_missing_shot(self):
        data, candidates = sample()
        report = build_selection(candidates, data)
        self.assertTrue(all(not i['cut_evidence'] for i in report['items']))
        self.assertEqual(report['summary']['proposal_count'], 0)

    def test_existing_shot_near_edge_is_only_a_review_proposal(self):
        data, _ = sample()
        data['shots'] = [{'start': 0, 'end': 17.9}, {'start': 17.9, 'end': 41}]
        report = build_selection(score_candidates(generate_candidates(data), data), data)
        item = next(i for i in report['items'] if i['proposal'])
        self.assertEqual(item['cut_evidence'][0]['source'], 'shot_detector')
        self.assertIn('scene_change_near_edge', item['issues'])

    def test_nms_respects_score_order_and_suppresses_only_against_kept_ranges(self):
        data, candidates = sample()
        report = build_selection(candidates, data)
        by_id = {c['id']: c for c in candidates['candidates']}
        retained = [by_id[i['candidate_id']] for i in report['items'] if i['retained']]
        self.assertTrue(report['items'][0]['retained'])
        for i in report['items']:
            if not i['retained']:
                self.assertIn(by_id[i['suppressed_by']], retained)
                self.assertGreaterEqual(i['overlap_iou'], .5)
        for index, a in enumerate(retained):
            self.assertTrue(all(temporal_iou(a, b) < .5 for b in retained[index+1:]))
        self.assertEqual(report, build_selection(candidates, data))

    def test_threshold_empty_and_tampered_input(self):
        data, candidates = sample()
        for threshold in (0, -1, 1.1, True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                build_selection(candidates, data, iou_threshold=threshold)
        candidates['candidates'][0]['score'] += 1
        with self.assertRaises(ValueError):
            build_selection(candidates, data)
        empty = fixture([])
        report = build_selection(score_candidates(generate_candidates(empty), empty), empty)
        self.assertEqual(report['items'], [])


class SelectionApiTests(unittest.TestCase):
    setUp = CandidateApiTests.setUp
    job = CandidateApiTests.job

    def reviewed_job(self):
        task = self.job()
        folder = job_dir(task)
        (folder / 'analysis.json').write_text(json.dumps(self.analysis), encoding='utf-8')
        job = get_job(task)
        job['result']['files']['analysis.json'] = 'analysis.json'
        with database() as connection:
            connection.execute('UPDATE jobs SET result_json = ? WHERE task_id = ?', (json.dumps(job['result']), task))
        return task

    def test_filter_is_global_and_download_matches_page(self):
        task = self.reviewed_job()
        original = (job_dir(task) / 'candidates.json').read_bytes()
        all_page = self.client.get(f'/tasks/{task}/candidates').json()
        self.assertEqual(all_page['selection_version'], 'boundary-nms-v1')
        report = self.client.get(f'/tasks/{task}/selection')
        self.assertEqual(report.status_code, 200)
        self.assertIn('attachment', report.headers['content-disposition'])
        retained = self.client.get(f'/tasks/{task}/candidates?view=retained&limit=1').json()
        expected = [i for i in report.json()['items'] if i['retained']]
        self.assertEqual(retained['total'], len(expected))
        self.assertEqual(retained['items'][0]['id'], expected[0]['candidate_id'])
        tail = self.client.get(f'/tasks/{task}/candidates?view=retained&offset={len(expected)}').json()
        self.assertEqual(tail['items'], [])
        self.assertEqual(original, (job_dir(task) / 'candidates.json').read_bytes())

    def test_legacy_and_invalid_review_are_not_silent_success(self):
        task = self.job()
        self.assertEqual(self.client.get(f'/tasks/{task}/candidates?view=retained').status_code, 409)
        self.assertEqual(self.client.get(f'/tasks/{task}/selection').status_code, 404)
        task = self.reviewed_job()
        (job_dir(task) / 'boundary_review.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.client.get(f'/tasks/{task}/candidates').status_code, 500)
        self.assertEqual(self.client.get(f'/tasks/{task}/selection').status_code, 500)
        self.assertEqual(self.client.get(f'/tasks/{task}/candidates?view=bad').status_code, 422)


if __name__ == '__main__':
    unittest.main()
