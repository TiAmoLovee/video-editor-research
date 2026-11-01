"""Duration/resource failure regressions without models, ASGI sockets or real disk exhaustion."""
from contextlib import contextmanager
import errno
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, Response, UploadFile

from clipforge.config import SourceDurationError, validate_source_duration
from clipforge.routes import video
from clipforge.services import pipeline
from clipforge.storage import jobs


class Week7FailurePathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {'CLIPFORGE_DATA_DIR': temporary.name,
                                     'CLIPFORGE_RESULT_CACHE': '0',
                                     'CLIPFORGE_MAX_SOURCE_SECONDS': '7200'})
        env.start()
        self.addCleanup(env.stop)

    def incoming(self, name='sample.mp4', size=5):
        result = UploadFile(filename=name, file=io.BytesIO(b'video'), size=size)
        self.addCleanup(result.file.close)
        return result

    def task(self):
        task_id = str(uuid4())
        folder = jobs.job_dir(task_id)
        folder.mkdir(parents=True)
        (folder / 'source.mp4').write_bytes(b'private source placeholder')
        jobs.create_job(task_id, 'test.mp4', 'source.mp4')
        return task_id, folder

    def test_default_accepts_sixty_minutes_and_exact_two_hour_boundary(self):
        for duration in (1, 3600, 7200):
            validate_source_duration({'duration_seconds': duration})
        with self.assertRaisesRegex(SourceDurationError, 'SOURCE_DURATION_LIMIT'):
            validate_source_duration({'duration_seconds': 7200.01})

    def test_configurable_limit_and_invalid_configuration_fail_closed(self):
        with patch.dict(os.environ, {'CLIPFORGE_MAX_SOURCE_SECONDS': '90'}):
            validate_source_duration({'duration_seconds': 90})
            with self.assertRaisesRegex(SourceDurationError, 'SOURCE_DURATION_LIMIT'):
                validate_source_duration({'duration_seconds': 90.1})
        for raw in ('0', '-1', '86401', 'NaN', '1.5', ''):
            with self.subTest(raw=raw), patch.dict(os.environ, {'CLIPFORGE_MAX_SOURCE_SECONDS': raw}):
                with self.assertRaisesRegex(SourceDurationError, 'SOURCE_DURATION_POLICY'):
                    validate_source_duration({'duration_seconds': 1})

    def test_unknown_and_non_finite_duration_never_pass_as_short_video(self):
        for duration in (None, True, '1', 0, -1, float('nan'), float('inf')):
            with self.subTest(duration=duration), self.assertRaisesRegex(SourceDurationError, 'SOURCE_DURATION_UNKNOWN'):
                validate_source_duration({'duration_seconds': duration})

    def test_overlong_source_fails_before_normalization_and_persists_safe_error(self):
        task_id, folder = self.task()
        with patch.object(pipeline, 'probe_video', return_value={}), \
             patch.object(pipeline, 'normalize_metadata', return_value={'duration_seconds': 7200.01}), \
             patch.object(pipeline, 'normalize_video') as normalize, \
             patch.object(pipeline, 'transcribe_video') as asr, \
             self.assertLogs('clipforge.services.pipeline', level='ERROR'):
            with self.assertRaisesRegex(SourceDurationError, 'SOURCE_DURATION_LIMIT'):
                pipeline.process_video(task_id)
        normalize.assert_not_called()
        asr.assert_not_called()
        saved = jobs.get_job(task_id)
        self.assertEqual((saved['status'], saved['stage']), ('FAILED', 'probing'))
        self.assertIn('[SOURCE_DURATION_LIMIT]', saved['error'])
        self.assertIsNone(saved['result'])
        self.assertEqual((folder / 'source.mp4').read_bytes(), b'private source placeholder')

    def test_stricter_current_duration_policy_cannot_be_bypassed_by_cache_hit(self):
        task_id, folder = self.task()

        @contextmanager
        def locked(key):
            yield folder / 'unused-cache-index.json'

        def restore(*args):
            (folder / 'media_meta.json').write_text(json.dumps({'duration_seconds': 91}), encoding='utf-8')
            return {'task_id': str(uuid4()), 'result': {}, 'files': {}}

        with patch.dict(os.environ, {'CLIPFORGE_RESULT_CACHE': '1', 'CLIPFORGE_MAX_SOURCE_SECONDS': '90'}), \
             patch.object(pipeline.result_cache, 'request_fingerprint', return_value=('key', {})), \
             patch.object(pipeline.result_cache, 'key_lock', side_effect=locked), \
             patch.object(pipeline.result_cache, 'restore', side_effect=restore), \
             patch.object(pipeline, '_process_uncached') as processing, \
             patch.object(pipeline, 'finish_task') as publish:
            with self.assertRaisesRegex(SourceDurationError, 'SOURCE_DURATION_LIMIT'):
                pipeline.process_video(task_id)
        processing.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(jobs.get_job(task_id)['status'], 'FAILED')
        self.assertIn('[SOURCE_DURATION_LIMIT]', jobs.get_job(task_id)['error'])

    def test_disk_full_during_upload_metadata_returns_507_and_cleans_only_new_task(self):
        preserved = self.root / 'jobs' / str(uuid4())
        preserved.mkdir(parents=True)
        (preserved / 'keep.txt').write_text('keep', encoding='utf-8')
        with patch.object(video, 'create_job', side_effect=OSError(errno.ENOSPC, 'private disk diagnostic')), \
             patch.object(video, 'submit_video') as submit, \
             self.assertLogs('clipforge.routes.video', level='ERROR'):
            with self.assertRaises(HTTPException) as caught:
                video.upload_video(Response(), self.incoming(), None)
        self.assertEqual(caught.exception.status_code, 507)
        self.assertIn('[STORAGE_FULL]', caught.exception.detail)
        self.assertNotIn('private', caught.exception.detail)
        submit.assert_not_called()
        self.assertEqual(list((self.root / 'jobs').iterdir()), [preserved])
        self.assertEqual((preserved / 'keep.txt').read_text(encoding='utf-8'), 'keep')

    def test_disk_full_before_directory_creation_is_also_handled(self):
        incoming = self.incoming()
        with patch('pathlib.Path.mkdir', side_effect=OSError(errno.ENOSPC, 'private')), \
             patch.object(video, 'submit_video') as submit, \
             self.assertLogs('clipforge.routes.video', level='ERROR'):
            with self.assertRaises(HTTPException) as caught:
                video.upload_video(Response(), incoming, None)
        self.assertEqual(caught.exception.status_code, 507)
        self.assertTrue(incoming.file.closed)
        submit.assert_not_called()

    def test_disk_full_during_destination_write_removes_partial_upload(self):
        original_open = Path.open

        class PartialWrite:
            def __init__(self, destination):
                self.destination = destination

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.destination.close()

            def write(self, data):
                self.destination.write(data[:2])
                self.destination.flush()
                raise OSError(errno.ENOSPC, 'private write detail')

        def opened(path, mode='r', *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            return PartialWrite(handle) if path.name == 'source.mp4' and mode == 'xb' else handle

        incoming = self.incoming()
        with patch.object(Path, 'open', new=opened), \
             patch.object(video, 'submit_video') as submit, \
             patch.object(video, 'create_job') as create, \
             self.assertLogs('clipforge.routes.video', level='ERROR'):
            with self.assertRaises(HTTPException) as caught:
                video.upload_video(Response(), incoming, None)
        self.assertEqual(caught.exception.status_code, 507)
        self.assertNotIn('private', caught.exception.detail)
        self.assertEqual(list((self.root / 'jobs').iterdir()), [])
        self.assertTrue(incoming.file.closed)
        create.assert_not_called()
        submit.assert_not_called()

    def test_sqlite_full_has_same_readable_storage_error(self):
        error = sqlite3.OperationalError('private SQLite detail')
        error.sqlite_errorcode = sqlite3.SQLITE_FULL
        with patch.object(video, 'create_job', side_effect=error), \
             self.assertLogs('clipforge.routes.video', level='ERROR'):
            with self.assertRaises(HTTPException) as caught:
                video.upload_video(Response(), self.incoming(), None)
        self.assertEqual(caught.exception.status_code, 507)
        self.assertNotIn('SQLite', caught.exception.detail)

    def test_other_storage_failure_returns_readable_503(self):
        with patch.object(video, 'create_job', side_effect=OSError(errno.EACCES, 'private permissions')), \
             self.assertLogs('clipforge.routes.video', level='ERROR'):
            with self.assertRaises(HTTPException) as caught:
                video.upload_video(Response(), self.incoming(), None)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn('[STORAGE_UNAVAILABLE]', caught.exception.detail)
        self.assertNotIn('private', caught.exception.detail)

    def test_oversize_file_and_empty_file_still_return_original_input_codes(self):
        for incoming, expected in ((self.incoming(size=video.MAX_UPLOAD_BYTES + 1), 413),
                                   (UploadFile(filename='empty.mp4', file=io.BytesIO(b''), size=0), 422)):
            with patch.object(video, 'submit_video') as submit:
                with self.assertRaises(HTTPException) as caught:
                    video.upload_video(Response(), incoming, None)
            self.assertEqual(caught.exception.status_code, expected)
            submit.assert_not_called()
            incoming.file.close()

    def test_chinese_filename_and_queue_uncertainty_contracts_remain(self):
        with patch.object(video, 'submit_video', side_effect=video.QueueUnavailable()):
            response = video.upload_video(Response(), self.incoming(name='中文 素材.mp4'), None)
        self.assertEqual(response.status_code, 503)
        saved = jobs.get_job(json.loads(response.body)['task_id'])
        self.assertEqual(saved['source_name'], '中文 素材.mp4')
        self.assertEqual(saved['source_key'], 'source.mp4')
        self.assertEqual(saved['status'], 'SUBMISSION_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
