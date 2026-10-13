"""Saved-revision ZIP publication, recovery, range binding and tamper rejection."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile, ZIP_STORED

from backend.tests import test_editing_service as fixtures
from clipforge.editing.render import _filter_graph
from clipforge.services import batch_export as batch
from clipforge.services import editing


class BatchExportServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.revision = fixtures.EditingServiceTests.create(self)
        self.revision_id = self.revision['revision_id']
        self.project = editing.load_task_edl(self.task_id, self.revision_id)['edl']
        self.request_id = str(uuid4())
        self.calls = []

    def fake_render(self, project, source_root, output, **options):
        self.calls.append(deepcopy(project))
        self.assertEqual(source_root, self.folder.resolve())
        self.assertEqual(len(project['clips']), 1)
        clip = project['clips'][0]
        self.assertEqual(clip['timeline_in_frame'], 0)
        self.assertEqual(clip['timeline_out_frame'], project['duration_frames'])
        Path(output).write_bytes(f"MP4 frame range {clip['source_in_frame']}:{clip['source_out_frame']}".encode())
        return {'artifact_kind': 'edl_render_verification', 'edl_sha256': batch._content_hash(project),
                'output': {'path': str(output), 'sha256': editing._digest(output),
                           'total_frames': project['duration_frames'], 'profile': project['profile']},
                'clips': _filter_graph(project)[2],
                **({'framing': project['framing']} if project['schema_version'] == '0.2.0' else {})}

    def create(self, request=None, revision=None, effect=None):
        with patch('clipforge.editing.render.render_edl', side_effect=effect or self.fake_render):
            return batch.create_task_batch_export(self.task_id, revision or self.revision_id, request or self.request_id)

    def location(self, record):
        return self.folder / 'edits' / record['revision_id'] / 'exports' / 'batch' / record['export_id']

    def rehash(self, folder):
        path = folder / 'batch-export.json'
        metadata = json.loads(path.read_text(encoding='utf-8'))
        metadata['files'] = {name: editing._digest(folder / name) for name in batch.FILES}
        metadata['output_bytes'] = (folder / 'clips.zip').stat().st_size
        path.write_text(json.dumps(metadata), encoding='utf-8')

    def test_each_saved_cut_is_rendered_and_zip_contains_verified_edl_manifest(self):
        before = (self.folder / 'normalized.mp4').read_bytes()
        record = self.create()
        result = batch.load_task_batch_export(self.task_id, self.revision_id, record['export_id'])
        self.assertEqual(result['export'], record)
        self.assertEqual(len(self.calls), len(self.project['clips']))
        with ZipFile(self.location(record) / 'clips.zip') as archive:
            self.assertEqual(archive.namelist(), ['source.edl.json', 'manifest.json', 'clips/001.mp4', 'clips/002.mp4'])
            self.assertEqual(json.loads(archive.read('source.edl.json')), self.project)
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest, result['manifest'])
            for clip, item in zip(self.project['clips'], manifest['clips'], strict=True):
                self.assertEqual(item['source_in_frame'], clip['source_in_frame'])
                self.assertEqual(item['source_out_frame'], clip['source_out_frame'])
                self.assertEqual(item['original_in_frame'], clip['provenance']['original_in_frame'])
                self.assertEqual(item['sha256'], hashlib.sha256(archive.read(item['filename'])).hexdigest())
        self.assertEqual((self.folder / 'normalized.mp4').read_bytes(), before)
        self.assertEqual((self.folder / 'result.zip').read_bytes(), b'old ZIP unchanged')
        self.assertEqual(set(path.name for path in self.location(record).iterdir()), batch.FILES | {'batch-export.json'})

    def test_request_id_is_idempotent_and_incomplete_request_is_not_rerendered(self):
        first, second = self.create(), self.create()
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), len(self.project['clips']))
        request = str(uuid4())
        parent = self.location(first).parent
        (parent / request).mkdir()
        with self.assertRaises(batch.ProjectError) as caught:
            self.create(request=request)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue((parent / request).exists())

    def test_revision_and_task_isolation_and_history(self):
        first = self.create()
        another = fixtures.EditingServiceTests.create(self, selected=list(reversed(self.selected)))
        second = self.create(revision=another['revision_id'])
        self.assertEqual(first['export_id'], second['export_id'])
        self.assertNotEqual(self.location(first), self.location(second))
        history = batch.list_task_batch_exports(self.task_id, self.revision_id)
        self.assertEqual(history['items'], [first])
        with self.assertRaises(batch.ProjectError):
            batch.load_task_batch_export(str(uuid4()), self.revision_id, first['export_id'])
        with self.assertRaises(batch.ProjectError):
            batch.load_task_batch_export(self.task_id, str(uuid4()), first['export_id'])

    def test_failures_remove_only_owned_incomplete_outputs(self):
        for error in (RuntimeError('private ffmpeg'), ValueError('private invalid'), OSError('private disk')):
            with self.subTest(error=error), self.assertRaises(batch.ProjectError) as caught:
                self.create(effect=error)
            self.assertNotIn('private', caught.exception.detail)
            self.assertFalse(self.location({'revision_id': self.revision_id, 'export_id': self.request_id}).exists())
        first = self.create()
        original = (self.location(first) / 'clips.zip').read_bytes()
        self.assertEqual(self.create(), first)
        self.assertEqual((self.location(first) / 'clips.zip').read_bytes(), original)

    def test_workload_and_bytes_limits_are_enforced_before_publication(self):
        with patch.object(batch, 'MAX_TOTAL_FRAMES', 1):
            with self.assertRaises(batch.ProjectError) as caught:
                self.create()
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.calls, [])
        with patch.object(batch, 'MAX_ARCHIVE_BYTES', 1):
            with self.assertRaises(batch.ProjectError) as caught:
                self.create()
            self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse(self.location({'revision_id': self.revision_id, 'export_id': self.request_id}).exists())

    def test_deadline_is_shared_across_clips_and_packaging(self):
        with patch.object(batch, '_budget', side_effect=batch.ProjectError(503, '超过时间。')):
            with self.assertRaises(batch.ProjectError):
                self.create()
        self.assertEqual(self.calls, [])

    def test_modified_files_and_source_are_rejected_and_hidden_by_history(self):
        for filename in batch.FILES:
            record = self.create(request=str(uuid4()))
            (self.location(record) / filename).write_bytes(b'changed')
            with self.subTest(filename=filename), self.assertRaises(batch.ProjectError):
                batch.load_task_batch_export(self.task_id, self.revision_id, record['export_id'])
        record = self.create(request=str(uuid4()))
        (self.folder / 'normalized.mp4').write_bytes(b'changed source')
        with self.assertRaises(batch.ProjectError):
            batch.load_task_batch_export(self.task_id, self.revision_id, record['export_id'])
        history = batch.list_task_batch_exports(self.task_id, self.revision_id)
        self.assertEqual(history['items'], [])
        self.assertEqual(history['unavailable_count'], 4)

    def test_zip_entry_tampering_is_rejected_even_if_outer_archive_hash_is_rewritten(self):
        for mode in ('clip', 'extra', 'duplicate', 'manifest'):
            record = self.create(request=str(uuid4()))
            folder = self.location(record)
            path = folder / 'clips.zip'
            with ZipFile(path) as archive:
                entries = [(entry.filename, archive.read(entry)) for entry in archive.infolist()]
            with ZipFile(path, 'w', compression=ZIP_STORED) as archive:
                for name, data in entries:
                    if mode == 'clip' and name == 'clips/001.mp4': data = b'different clip'
                    if mode == 'manifest' and name == 'manifest.json': data = b'{}'
                    archive.writestr(name, data)
                if mode == 'extra': archive.writestr('../normalized.mp4', b'unsafe')
                if mode == 'duplicate': archive.writestr('clips/001.mp4', b'duplicate')
            self.rehash(folder)
            with self.subTest(mode=mode), self.assertRaises(batch.ProjectError) as caught:
                batch.load_task_batch_export(self.task_id, self.revision_id, record['export_id'])
            self.assertEqual(caught.exception.status_code, 409)

    def test_rehashed_manifest_cannot_change_final_or_original_ranges(self):
        for field in ('source_in_frame', 'original_in_frame'):
            record = self.create(request=str(uuid4()))
            folder = self.location(record)
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            manifest['clips'][0][field] += 1
            changed = json.dumps(manifest).encode('utf-8')
            (folder / 'manifest.json').write_bytes(changed)
            with ZipFile(folder / 'clips.zip') as archive:
                entries = [(entry.filename, archive.read(entry)) for entry in archive.infolist()]
            with ZipFile(folder / 'clips.zip', 'w', compression=ZIP_STORED) as archive:
                for name, data in entries:
                    archive.writestr(name, changed if name == 'manifest.json' else data)
            self.rehash(folder)
            with self.subTest(field=field), self.assertRaises(batch.ProjectError):
                batch.load_task_batch_export(self.task_id, self.revision_id, record['export_id'])

    def test_held_download_handle_and_filename_uuid_allowlist(self):
        record = self.create()
        for filename in batch.FILES:
            with batch.open_batch_export_file(self.task_id, self.revision_id, record['export_id'], filename) as incoming:
                if os.name == 'nt':
                    with self.assertRaises(PermissionError):
                        (self.location(record) / filename).write_bytes(b'replaced')
                self.assertTrue(incoming.read())
            self.assertTrue(incoming.closed)
        for filename in ('../clips.zip', 'normalized.mp4', 'batch-export.json', 'clips/001.mp4'):
            with self.subTest(filename=filename), self.assertRaises(batch.ProjectError) as caught:
                with batch.open_batch_export_file(self.task_id, self.revision_id, record['export_id'], filename):
                    self.fail('unsafe file opened')
            self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(batch.ProjectError):
            batch.load_task_batch_export(self.task_id, self.revision_id, '../outside')


if __name__ == '__main__':
    unittest.main()
