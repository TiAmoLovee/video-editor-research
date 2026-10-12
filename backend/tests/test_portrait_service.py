"""Saved portrait revisions bind native snapshots, parents and crop records."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.tests import test_editing_service as fixtures
from clipforge.services import editing, rendering, subtitling
from clipforge.storage import jobs


class PortraitServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.original = fixtures.EditingServiceTests.create(self)
        self.parent_id = self.original['revision_id']
        self.parent_folder = self.folder / 'edits' / self.parent_id

    def create(self):
        return editing.create_task_portrait(self.task_id, self.parent_id)

    def tamper_record(self, record, filename, value):
        folder = self.folder / 'edits' / record['revision_id']
        (folder / filename).write_text(json.dumps(value), encoding='utf-8')
        if filename != 'revision.json':
            record = deepcopy(record)
            record['files'][filename] = editing._digest(folder / filename)
            (folder / 'revision.json').write_text(json.dumps(record), encoding='utf-8')

    def test_derivation_preserves_native_snapshots_original_task_and_parent(self):
        before = jobs.get_job(self.task_id)
        files = {p.name: p.read_bytes() for p in self.parent_folder.iterdir() if p.is_file()}
        record = self.create()
        saved = editing.load_task_edl(self.task_id, record['revision_id'])
        original = editing.load_task_edl(self.task_id, self.parent_id)
        self.assertEqual(saved['edl']['profile'], {'width': 1080, 'height': 1920, 'fps': self.source['fps']})
        self.assertEqual(saved['manifest'], original['manifest'])
        for field in ('sources', 'clips', 'duration_frames'):
            self.assertEqual(saved['edl'][field], original['edl'][field])
        for name in editing.FILES - {'project.edl.json'}:
            self.assertEqual(record['files'][name], self.original['files'][name])
        self.assertEqual(record['derivation']['parent_edl_file_sha256'], self.original['files']['project.edl.json'])
        self.assertEqual(jobs.get_job(self.task_id), before)
        self.assertTrue(all((self.parent_folder / n).read_bytes() == b for n, b in files.items()))
        self.assertEqual((self.folder / 'result.zip').read_bytes(), b'old ZIP unchanged')
        with editing.open_revision_source(self.task_id, record['revision_id']) as incoming:
            self.assertEqual(incoming.read(), self.media)

    def test_uses_saved_snapshots_when_current_analysis_is_unavailable(self):
        (self.folder / 'analysis.json').write_text('unavailable current ASR', encoding='utf-8')
        (self.folder / 'candidates.json').write_text('unavailable current ranking', encoding='utf-8')
        record = self.create()
        subtitle = subtitling.create_task_subtitles(self.task_id, record['revision_id'])
        plan = subtitling.load_task_subtitles(self.task_id, record['revision_id'], subtitle['subtitle_id'])['plan']
        self.assertEqual(plan['profile']['width'], 1080)
        self.assertEqual(plan['policy']['target_cjk_characters_per_line'], 12)
        self.assertEqual(subtitle['analysis_file_sha256'], self.original['files']['analysis.json'])

    def test_changed_media_and_portrait_parent_rejected_without_partial_revision(self):
        first = self.create()
        with self.assertRaises(editing.ProjectError):
            editing.create_task_portrait(self.task_id, first['revision_id'])
        (self.folder / 'normalized.mp4').write_bytes(b'changed native source')
        with self.assertRaises(editing.ProjectError):
            self.create()
        self.assertEqual(len(list((self.folder / 'edits').iterdir())), 2)

    def test_parent_hash_loop_and_changed_snapshot_rejected_even_with_rehashed_record(self):
        for change in ('hash', 'loop', 'snapshot'):
            record = self.create()
            value = deepcopy(record)
            if change == 'snapshot':
                analysis = json.loads((self.parent_folder / 'analysis.json').read_text(encoding='utf-8'))
                analysis['synthetic_example'] = True
                self.tamper_record(record, 'analysis.json', analysis)
            else:
                if change == 'hash':
                    value['derivation']['parent_edl_file_sha256'] = '0' * 64
                else:
                    value['derivation']['parent_revision_id'] = record['revision_id']
                self.tamper_record(record, 'revision.json', value)
            with self.subTest(change=change), self.assertRaises(editing.ProjectError):
                editing.load_task_edl(self.task_id, record['revision_id'])

    def test_failures_and_uuid_collision_keep_existing_revision(self):
        before = {p.name: p.read_bytes() for p in self.parent_folder.iterdir() if p.is_file()}
        with patch.object(editing, 'uuid4', return_value=self.parent_id):
            with self.assertRaises(editing.ProjectError):
                self.create()
        self.assertTrue(all((self.parent_folder / n).read_bytes() == b for n, b in before.items()))
        with patch.object(editing, '_write_json', side_effect=OSError('private disk failure')):
            with self.assertRaises(editing.ProjectError) as caught:
                self.create()
        self.assertNotIn('private', caught.exception.detail)
        self.assertEqual([p.name for p in self.parent_folder.parent.iterdir()], [self.parent_id])

    def test_fresh_subtitle_layout_does_not_modify_or_reuse_old_version(self):
        old = subtitling.create_task_subtitles(self.task_id, self.parent_id)
        old_saved = subtitling.load_task_subtitles(self.task_id, self.parent_id, old['subtitle_id'])
        record = self.create()
        new = subtitling.create_task_subtitles(self.task_id, record['revision_id'])
        self.assertEqual(subtitling.load_task_subtitles(self.task_id, self.parent_id, old['subtitle_id']), old_saved)
        self.assertNotEqual(old['plan_content_sha256'], new['plan_content_sha256'])
        with self.assertRaises(editing.ProjectError):
            subtitling.load_task_subtitles(self.task_id, record['revision_id'], old['subtitle_id'])

    def render(self, record, bad_crop=False):
        project = editing.load_task_edl(self.task_id, record['revision_id'])['edl']

        def fake_render(edl, source_root, output, **options):
            self.assertEqual(edl, project)
            Path(output).write_bytes(b'independently audited native render has separate evidence')
            return {'artifact_kind': 'edl_render_verification', 'edl_sha256': rendering._content_hash(edl),
                    'framing': {} if bad_crop else deepcopy(edl['framing']),
                    'output': {'path': str(output), 'sha256': rendering._digest(output),
                               'profile': edl['profile'], 'total_frames': edl['duration_frames']}}
        with patch('clipforge.editing.render.render_edl', side_effect=fake_render):
            return rendering.create_task_render(self.task_id, record['revision_id'])

    def test_render_record_requires_saved_crop_and_download_uses_same_hashed_handle(self):
        record = self.create()
        with self.assertRaises(editing.ProjectError):
            self.render(record, bad_crop=True)
        rendered = self.render(record)
        saved = rendering.load_task_render(self.task_id, record['revision_id'], rendered['render_id'])
        self.assertEqual(saved['verification']['framing'], editing.load_task_edl(self.task_id, record['revision_id'])['edl']['framing'])
        with rendering.open_render_file(self.task_id, record['revision_id'], rendered['render_id'], 'output.mp4') as incoming:
            self.assertEqual(hashlib.file_digest(incoming, 'sha256').hexdigest(), rendered['files']['output.mp4'])
        folder = self.folder / 'edits' / record['revision_id'] / 'renders' / rendered['render_id']
        report = deepcopy(saved['verification'])
        report['framing']['sources'][0]['x'] += 2
        (folder / 'verification.json').write_text(json.dumps(report), encoding='utf-8')
        modified = deepcopy(rendered)
        modified['files']['verification.json'] = rendering._digest(folder / 'verification.json')
        (folder / 'render.json').write_text(json.dumps(modified), encoding='utf-8')
        with self.assertRaises(editing.ProjectError):
            rendering.load_task_render(self.task_id, record['revision_id'], rendered['render_id'])


if __name__ == '__main__':
    unittest.main()
