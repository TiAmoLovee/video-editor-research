"""Batch API requires an explicit UUID request, safe errors and verified streams."""

from contextlib import contextmanager
import io
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from clipforge.routes import batch_export as routes
from clipforge.services.editing import ProjectError

TASK = UUID('00000000-0000-4000-8000-000000000001')
REVISION = UUID('00000000-0000-4000-8000-000000000002')
EXPORT = UUID('00000000-0000-4000-8000-000000000003')
ROOT = f'/tasks/{TASK}/edl/{REVISION}/exports/batch'


class BatchExportRoutesTests(unittest.TestCase):
    def test_post_has_explicit_id_and_saved_revision_location(self):
        metadata = {'task_id': str(TASK), 'revision_id': str(REVISION), 'export_id': str(EXPORT), 'request_id': str(EXPORT)}
        with patch.object(routes.service, 'create_task_batch_export', return_value=metadata) as create:
            response = routes.create_batch_export(TASK, REVISION, routes.BatchExportRequest(request_id=EXPORT))
        create.assert_called_once_with(str(TASK), str(REVISION), str(EXPORT), timeout=90)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers['location'], f'{ROOT}/{EXPORT}')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(json.loads(response.body), metadata)

    def test_list_get_belong_to_path_revision(self):
        with patch.object(routes.service, 'list_task_batch_exports', return_value={'items': []}) as list_exports:
            self.assertEqual(json.loads(routes.list_batch_exports(TASK, REVISION).body), {'items': []})
        list_exports.assert_called_once_with(str(TASK), str(REVISION))
        with patch.object(routes.service, 'load_task_batch_export', return_value={'export': {}}) as load:
            self.assertEqual(json.loads(routes.get_batch_export(TASK, REVISION, EXPORT).body), {'export': {}})
        load.assert_called_once_with(str(TASK), str(REVISION), str(EXPORT))

    def test_uuid_validation_and_no_extra_client_render_knobs(self):
        app = FastAPI(); app.include_router(routes.router)
        for body in ({}, {'request_id': '../unsafe'}, {'request_id': str(EXPORT), 'ffmpeg': 'private'}, {'request_id': True}):
            with self.assertRaises(ValidationError):
                routes.BatchExportRequest.model_validate(body)
        operation = app.openapi()['paths']['/tasks/{task_id}/edl/{revision_id}/exports/batch']['post']
        self.assertIn('90', operation['description'])
        self.assertIn('requestBody', operation)
        self.assertEqual({parameter['name'] for parameter in operation['parameters']}, {'task_id', 'revision_id'})
        self.assertTrue(all(parameter['schema']['format'] == 'uuid' for parameter in operation['parameters']))
        schema = app.openapi()['components']['schemas']['BatchExportRequest']
        self.assertEqual(schema['properties']['request_id']['format'], 'uuid')
        self.assertFalse(schema['additionalProperties'])

    def test_download_holds_and_closes_verified_handle_with_zip_media_type(self):
        incoming = io.BytesIO(b'ZIP bytes')
        @contextmanager
        def resource():
            try: yield incoming
            finally: incoming.close()
        with patch.object(routes.service, 'open_batch_export_file', return_value=resource()) as opened:
            response = routes.download_batch_export_file(TASK, REVISION, EXPORT, 'clips.zip')
        opened.assert_called_once_with(str(TASK), str(REVISION), str(EXPORT), 'clips.zip')
        self.assertEqual(response.media_type, 'application/zip')
        self.assertEqual(b''.join(response.iter_bytes()), b'ZIP bytes')
        self.assertTrue(incoming.closed)

    def test_safe_service_errors_and_private_error_masking(self):
        for status in (404, 409, 422, 503):
            with patch.object(routes.service, 'list_task_batch_exports', side_effect=ProjectError(status, '安全错误。')):
                with self.assertRaises(HTTPException) as caught: routes.list_batch_exports(TASK, REVISION)
                self.assertEqual(caught.exception.status_code, status)
        with patch.object(routes.service, 'create_task_batch_export', side_effect=RuntimeError('private source path')):
            with patch.object(routes.logger, 'exception'), self.assertRaises(HTTPException) as caught:
                routes.create_batch_export(TASK, REVISION, routes.BatchExportRequest(request_id=EXPORT))
        self.assertEqual(caught.exception.status_code, 500)
        self.assertNotIn('private', caught.exception.detail)


if __name__ == '__main__':
    unittest.main()
