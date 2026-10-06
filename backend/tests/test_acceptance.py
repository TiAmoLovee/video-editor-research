from copy import deepcopy
import json
import unittest

from backend.tests import test_candidate_api as fixtures
from backend.tests import test_reviewed as reviewed_fixtures
from clipforge.decision.assessment import evaluate_assessment
from clipforge.decision.acceptance import checked_entry, install, load_accepted
from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash
from clipforge.storage.jobs import job_dir, get_job, database


class AcceptanceTests(unittest.TestCase):
    setUp = fixtures.CandidateApiTests.setUp
    job = fixtures.CandidateApiTests.job

    def ready(self):
        task = self.job()
        folder = job_dir(task)
        (folder/'analysis.json').write_text(json.dumps(self.analysis), encoding='utf-8')
        result = get_job(task)['result']
        result['files']['analysis.json'] = 'analysis.json'
        with database() as db:
            db.execute('UPDATE jobs SET result_json=? WHERE task_id=?', (json.dumps(result), task))
        candidate = self.result['candidates'][0]
        media = folder/'fixture.mp4'
        media.write_bytes(b'test-only media identity')
        a = {'version': 'human-candidate-acceptance-v1', 'task_id': task,
             'user_feedback_verbatim': '开头能理解，结尾完整，没有无关内容', 'status': 'accepted_by_user',
             'checks': {'opening_understandable': True, 'ending_complete': True, 'unrelated_content_included': False},
             'analysis_sha256': content_hash(self.analysis), 'original_candidates_sha256': content_hash(self.result),
             'source_sha256': self.analysis['media']['source_sha256'],
             'normalized_sha256': self.analysis['media']['normalized_sha256'],
             'candidate_id': candidate['id'], 'selected_sha256': file_hash(media),
             'original_candidate_range': {'start': candidate['start'], 'end': candidate['end']},
             'rendered_range': {'fps': 30, 'start_frame': int(candidate['start']*30),
                                'end_frame_exclusive': int(candidate['end']*30),
                                'start_seconds': candidate['start'], 'end_seconds_exclusive': candidate['end'],
                                'duration_seconds': candidate['duration_seconds']}}
        return task, folder, a, media

    def test_import_is_idempotent_and_api_media_and_pagination_keep_acceptance(self):
        task, folder, a, media = self.ready()
        original = (folder/'candidates.json').read_bytes()
        for _ in range(2):
            entry = install(folder, task, self.analysis, self.result, a, media)
        self.assertEqual(len(load_accepted(folder, task, self.analysis, self.result)), 1)
        for query in ('?limit=1', '?limit=1&offset=1', '?view=review'):
            response = self.client.get(f'/tasks/{task}/candidates{query}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['accepted_versions'][0]['id'], entry['id'])
        self.assertEqual(self.client.get(entry['video_url']).content, media.read_bytes())
        self.assertEqual(self.client.get(f'/tasks/{task}/accepted/unknown.mp4').status_code, 404)
        self.assertEqual((folder/'candidates.json').read_bytes(), original)

    def test_rejects_wrong_task_hash_feedback_and_range(self):
        task, folder, a, media = self.ready()
        edits = [lambda x: x.update(task_id='another-task'), lambda x: x.update(user_feedback_verbatim=''),
                 lambda x: x.update(analysis_sha256='0'*64),
                 lambda x: x['checks'].update(ending_complete=False),
                 lambda x: x['checks'].update(ending_complete=1),
                 lambda x: x['rendered_range'].update(end_seconds_exclusive=123),
                 lambda x: x['rendered_range'].update(start_frame=True)]
        for edit in edits:
            bad = deepcopy(a); edit(bad)
            with self.assertRaises(ValueError):
                checked_entry(task, self.analysis, self.result, bad)
        media.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            install(folder, task, self.analysis, self.result, a, media)

    def test_changed_stored_media_is_not_served_or_labeled_accepted(self):
        task, folder, a, media = self.ready()
        entry = install(folder, task, self.analysis, self.result, a, media)
        (folder/'accepted'/f'{entry["id"]}.mp4').write_bytes(b'corrupt')
        self.assertEqual(self.client.get(entry['video_url']).status_code, 500)
        self.assertEqual(self.client.get(f'/tasks/{task}/candidates').status_code, 500)

    def test_different_feedback_cannot_silently_replace_history(self):
        task, folder, a, media = self.ready()
        install(folder, task, self.analysis, self.result, a, media)
        a['user_feedback_verbatim'] = 'a different decision'
        with self.assertRaises(ValueError):
            install(folder, task, self.analysis, self.result, a, media)

    def test_reviewed_model_version_keeps_new_score_and_rejects_changed_provenance(self):
        fixture = reviewed_fixtures.ReviewedCandidateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        record = fixture.build()
        assessment = {name: {'score': points, 'finding': finding, 'quote': '面对这种局面'}
                      for name, points, finding in [('completeness', 30, 'needs_context'),
                          ('clarity', 20, 'clear'), ('engagement', 15, 'concrete_detail'),
                          ('conciseness', 5, 'concise')]}
        record['candidate'].update(evaluate_assessment(assessment, record['candidate']['text']), scorer='llm')
        record['model_scoring'] = {'status': 'passed'}
        a = fixture.acceptance
        entry = checked_entry(a['task_id'], fixture.analysis, fixture.scored, a, record)
        self.assertEqual(entry['score'], 70)
        self.assertEqual(entry['score_scope'], 'rendered_range')
        for edit in (lambda r: r['candidate'].update(score=99),
                     lambda r: r['candidate'].update(text='changed'),
                     lambda r: r['provenance'].update(acceptance_sha256='f'*64)):
            changed = deepcopy(record); edit(changed)
            with self.assertRaises(ValueError):
                checked_entry(a['task_id'], fixture.analysis, fixture.scored, a, changed)
