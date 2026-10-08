from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch

from backend.tests import test_candidate_api as fixtures
from backend.tests import test_reviewed as reviewed_fixtures
from clipforge.decision.assessment import evaluate_assessment
from clipforge.decision.acceptance import checked_entry, install, load_accepted
from clipforge.decision.candidates import content_hash
from clipforge.decision.reviewed import file_hash
from clipforge.storage.jobs import job_dir, get_job, database
from clipforge.decision.evaluation import freeze, QUESTIONS


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

    def form_record(self):
        task, folder, original, media = self.ready()
        manifest = freeze(self.analysis, self.result, 'example.mp4')
        sample = manifest['samples'][0]
        digest = file_hash(media)
        manifest['renders'] = {sample['id']: {'file':sample['file'], 'sha256':digest,
            'frames': sample['end_frame_exclusive']-sample['start_frame'], 'duration_seconds':sample['duration_seconds']}}
        review = {'batch_id':manifest['batch_id'], 'manifest_sha256':content_hash(manifest), 'revision':1,
                  'reviews':{sample['id']:{'answers':dict.fromkeys(QUESTIONS, 'yes'), 'note':'',
                                          'provenance':'user_submitted_local_form'}}}
        a = {'version':'human-evaluation-acceptance-v1','task_id':task,'sample_id':sample['id'],
             'selected_sha256':digest,'evaluation':{'manifest':manifest,'human_review':review}}
        return task, folder, a, media

    def test_structured_form_import_keeps_provenance_without_fabricated_quote(self):
        task, folder, a, media = self.form_record()
        before = (folder/'candidates.json').read_bytes()
        item = install(folder, task, self.analysis, self.result, a, media)
        self.assertTrue(item['feedback'].startswith('表单评审：'))
        self.assertNotIn('user_feedback_verbatim', a)
        self.assertEqual(item['review_provenance']['kind'], 'structured_form')
        self.assertEqual(item['score_scope'], 'original_candidate_range')
        self.assertEqual(len(load_accepted(folder, task, self.analysis, self.result)), 1)
        self.assertEqual(self.client.get(item['video_url']).status_code, 200)
        self.assertEqual(before, (folder/'candidates.json').read_bytes())
        with patch('clipforge.decision.selection.VERSION', 'future-selector-version'):
            self.assertEqual(checked_entry(task, self.analysis, self.result, a), item)

    def test_form_import_rejects_failed_uncertain_wrong_candidate_and_changed_media(self):
        task, folder, a, media = self.form_record()
        sid = a['sample_id']
        edits = [lambda x: x['evaluation']['human_review']['reviews'][sid]['answers'].update(ending_complete='no'),
                 lambda x: x['evaluation']['human_review']['reviews'][sid]['answers'].update(understandable='uncertain'),
                 lambda x: x['evaluation']['human_review'].update(manifest_sha256='0'*64),
                 lambda x: x['evaluation']['manifest']['samples'][0].update(candidate_id='other'),
                 lambda x: x['evaluation']['human_review']['reviews'][sid].update(provenance='automatic'),
                 lambda x: x.update(selected_sha256='f'*64),
                 lambda x: x['evaluation']['human_review'].update(revision=True)]
        for edit in edits:
            bad=deepcopy(a); edit(bad)
            with self.assertRaises(ValueError):
                checked_entry(task,self.analysis,self.result,bad)

    def test_rehashed_form_cannot_relabel_original_text_score_or_timing(self):
        task, folder, a, media = self.form_record()
        for edit in (lambda s:s.update(text='a different transcript'),
                     lambda s:s.update(original_score=99),
                     lambda s:s.update(original_rank=999),
                     lambda s:s.update(start_frame=s['start_frame']+1)):
            bad=deepcopy(a)
            manifest=bad['evaluation']['manifest']
            edit(manifest['samples'][0])
            manifest['batch_id']=content_hash({k:v for k,v in manifest.items() if k not in ('batch_id','renders','prepared_at')})
            review=bad['evaluation']['human_review']
            review.update(batch_id=manifest['batch_id'],manifest_sha256=content_hash(manifest))
            with self.assertRaises(ValueError):
                checked_entry(task,self.analysis,self.result,bad)

    def test_different_feedback_cannot_silently_replace_history(self):
        task, folder, a, media = self.ready()
        install(folder, task, self.analysis, self.result, a, media)
        a['user_feedback_verbatim'] = 'a different decision'
        with self.assertRaises(ValueError):
            install(folder, task, self.analysis, self.result, a, media)

    def test_interrupted_copy_leaves_import_retryable_and_unpublished(self):
        task, folder, a, media = self.ready()
        def interrupted_copy(src, dst):
            dst.write(b'partial')
            self.assertEqual(load_accepted(folder, task, self.analysis, self.result), [])
            raise OSError('simulated interrupted copy')
        with patch('clipforge.decision.acceptance.shutil.copyfileobj', side_effect=interrupted_copy):
            with self.assertRaises(OSError):
                install(folder, task, self.analysis, self.result, a, media)
        self.assertEqual(list((folder/'accepted').iterdir()), [])
        item = install(folder, task, self.analysis, self.result, a, media)
        self.assertEqual(self.client.get(item['video_url']).content, media.read_bytes())

    def test_failed_record_publication_keeps_valid_media_and_allows_retry(self):
        task, folder, a, media = self.ready()
        real_link = os.link
        def fail_record(source, destination):
            self.assertEqual(load_accepted(folder, task, self.analysis, self.result), [])
            if destination.suffix == '.json':
                raise OSError('simulated record publication failure')
            real_link(source, destination)
        with patch('clipforge.decision.acceptance.os.link', side_effect=fail_record):
            with self.assertRaises(OSError):
                install(folder, task, self.analysis, self.result, a, media)
        self.assertEqual(list((folder/'accepted').glob('*.json')), [])
        item = install(folder, task, self.analysis, self.result, a, media)
        self.assertEqual(len(load_accepted(folder, task, self.analysis, self.result)), 1)
        self.assertEqual(self.client.get(item['video_url']).content, media.read_bytes())

    def test_copy_changed_during_import_is_not_published(self):
        task, folder, a, media = self.ready()
        def changed_copy(src, dst):
            dst.write(b'changed after original hash check')
        with patch('clipforge.decision.acceptance.shutil.copyfileobj', side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError, '复制期间'):
                install(folder, task, self.analysis, self.result, a, media)
        self.assertEqual(list((folder/'accepted').iterdir()), [])

    def test_concurrent_record_is_never_overwritten(self):
        task, folder, a, media = self.ready()
        real_link = os.link
        existing = {'acceptance': {**a, 'user_feedback_verbatim': 'another reviewer'}, 'reviewed': None}
        def race_record(source, destination):
            if destination.suffix == '.json':
                destination.write_text(json.dumps(existing), encoding='utf-8')
            real_link(source, destination)
        with patch('clipforge.decision.acceptance.os.link', side_effect=race_record):
            with self.assertRaisesRegex(ValueError, '不同验收记录'):
                install(folder, task, self.analysis, self.result, a, media)
        record = folder/'accepted'/f'{a["selected_sha256"]}.json'
        self.assertEqual(json.loads(record.read_text(encoding='utf-8')), existing)

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
