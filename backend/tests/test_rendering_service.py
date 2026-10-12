"""Render publication is bound to the saved EDL, not current candidate rankings."""

from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.tests import test_editing_service as fixtures
from clipforge.services import rendering
from clipforge.storage import jobs


class RenderingServiceTests(unittest.TestCase):
    # Keep this module's scope focused on rendering; the base module verifies
    # revision creation and supplies setup/fixtures, not copied implementation.
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.revision = fixtures.EditingServiceTests.create(self)
        self.revision_id = self.revision['revision_id']
        self.revision_folder = self.folder / 'edits' / self.revision_id
        self.project = json.loads((self.revision_folder / 'project.edl.json').read_text(encoding='utf-8'))

    def run_render(self, effect=None):
        def fake_render(project, source_root, output, **options):
            self.assertEqual(project, self.project)
            self.assertEqual(source_root, self.folder.resolve())
            Path(output).write_bytes(b'stub MP4; real renderer is separately media-tested')
            return {'schema_version': '0.1.0', 'artifact_kind': 'edl_render_verification',
                    'edl_sha256': rendering._content_hash(project),
                    'output': {'path': str(output), 'sha256': rendering._digest(output),
                               'total_frames': project['duration_frames'], 'profile': project['profile']}}
        with patch('clipforge.editing.render.render_edl', side_effect=effect or fake_render):
            return rendering.create_task_render(self.task_id, self.revision_id)

    def test_render_reads_saved_edl_and_preserves_legacy_result_and_revision(self):
        before = jobs.get_job(self.task_id)
        original = {name: (self.revision_folder / name).read_bytes()
                    for name in ('project.edl.json', 'media-profile.json', 'revision.json')}
        (self.folder / 'candidates.json').write_text('current ranking unavailable', encoding='utf-8')
        metadata = self.run_render()
        saved = rendering.load_task_render(self.task_id, self.revision_id, metadata['render_id'])
        self.assertEqual(saved['render'], metadata)
        self.assertEqual(metadata['edl_file_sha256'], self.revision['files']['project.edl.json'])
        self.assertEqual(before, jobs.get_job(self.task_id))
        self.assertTrue(all((self.revision_folder / name).read_bytes() == value for name, value in original.items()))
        for filename in rendering.RENDER_FILES:
            with rendering.open_render_file(self.task_id, self.revision_id, metadata['render_id'], filename) as stream:
                self.assertTrue(stream.read())
        self.assertEqual((self.folder / 'result.zip').read_bytes(), b'old ZIP unchanged')

    def test_failed_or_inconsistent_render_does_not_publish_partial_files(self):
        for effect in (RuntimeError('private tool details'), ValueError('invalid'), OSError('disk full')):
            with self.subTest(effect=effect), self.assertRaises(rendering.ProjectError) as caught:
                self.run_render(effect=effect)
            self.assertNotIn('private tool details', caught.exception.detail)
        self.assertEqual(list((self.revision_folder / 'renders').iterdir()), [])
        def wrong_output(project, source_root, output, **kwargs):
            Path(output).write_bytes(b'partial')
            return {'artifact_kind': 'edl_render_verification', 'edl_sha256': 'f' * 64,
                    'output': {'sha256': rendering._digest(output)}}
        with self.assertRaises(rendering.ProjectError):
            self.run_render(effect=wrong_output)
        self.assertEqual(list((self.revision_folder / 'renders').iterdir()), [])

    def test_repeat_renders_keep_previous_outputs_and_snapshot(self):
        first, second = self.run_render(), self.run_render()
        self.assertNotEqual(first['render_id'], second['render_id'])
        for record in (first, second):
            folder = self.revision_folder / 'renders' / record['render_id']
            self.assertEqual(json.loads((folder / 'source.edl.json').read_text(encoding='utf-8')), self.project)

    def test_changed_render_files_or_missing_completion_record_rejected(self):
        for filename in rendering.RENDER_FILES:
            metadata = self.run_render()
            folder = self.revision_folder / 'renders' / metadata['render_id']
            (folder / filename).write_bytes(b'changed')
            with self.subTest(filename=filename), self.assertRaises(rendering.ProjectError):
                rendering.load_task_render(self.task_id, self.revision_id, metadata['render_id'])
        metadata = self.run_render()
        (self.revision_folder / 'renders' / metadata['render_id'] / 'render.json').unlink()
        with self.assertRaises(rendering.ProjectError) as caught:
            rendering.load_task_render(self.task_id, self.revision_id, metadata['render_id'])
        self.assertEqual(caught.exception.status_code, 404)

    def test_render_metadata_identity_timeline_and_edl_hash_are_checked(self):
        metadata = self.run_render()
        path = self.revision_folder / 'renders' / metadata['render_id'] / 'render.json'
        for change in ({'task_id': 'other'}, {'total_frames': 1}, {'edl_file_sha256': '0' * 64},
                       {'edl_content_sha256': '0' * 64}, {'output_bytes': True}):
            mutated = deepcopy(metadata)
            mutated.update(change)
            path.write_text(json.dumps(mutated), encoding='utf-8')
            with self.subTest(change=change), self.assertRaises(rendering.ProjectError):
                rendering.load_task_render(self.task_id, self.revision_id, metadata['render_id'])

    def test_render_download_is_held_handle_and_disallows_arbitrary_paths(self):
        metadata = self.run_render()
        output = self.revision_folder / 'renders' / metadata['render_id'] / 'output.mp4'
        with rendering.open_render_file(self.task_id, self.revision_id, metadata['render_id'], 'output.mp4') as stream:
            if os.name == 'nt':
                with self.assertRaises(PermissionError):
                    output.write_bytes(b'replaced')
            self.assertIn(b'stub MP4', stream.read())
        self.assertTrue(stream.closed)
        for filename in ('../output.mp4', 'render.json', 'normalized.mp4'):
            with self.subTest(filename=filename), self.assertRaises(rendering.ProjectError) as caught:
                with rendering.open_render_file(self.task_id, self.revision_id, metadata['render_id'], filename):
                    self.fail('invalid path must not open')
            self.assertEqual(caught.exception.status_code, 404)

    def test_identifier_collision_never_removes_an_existing_render(self):
        first = self.run_render()
        existing = self.revision_folder / 'renders' / first['render_id']
        original = {p.name: p.read_bytes() for p in existing.iterdir()}
        with patch.object(rendering, 'uuid4', return_value=first['render_id']):
            with self.assertRaises(rendering.ProjectError):
                self.run_render()
        self.assertEqual({p.name: p.read_bytes() for p in existing.iterdir()}, original)


if __name__ == '__main__':
    unittest.main()
