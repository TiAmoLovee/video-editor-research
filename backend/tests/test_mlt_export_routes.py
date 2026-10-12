"""Immutable MLT API paths, no client geometry, and held download contracts."""

from contextlib import contextmanager
import io
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

from clipforge.api import app
from clipforge.routes import editing
from clipforge.services.editing import ProjectError

TASK, REVISION, EXPORT, SUBTITLE = map(UUID, (
    "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003", "00000000-0000-4000-8000-000000000004"))


class MltExportRoutesTests(unittest.TestCase):
    def test_plain_and_subtitled_create_bind_saved_ids_and_return_location(self):
        record = {"export_id": str(EXPORT)}
        for subtitle_id, call in ((None, lambda: editing.create_mlt_export(TASK, REVISION)),
                                  (str(SUBTITLE), lambda: editing.create_subtitled_mlt_export(TASK, REVISION, SUBTITLE))):
            with patch.object(editing.mlt_exporting_service, "create_task_mlt_export", return_value=record) as create:
                response = call()
            create.assert_called_once_with(str(TASK), str(REVISION), subtitle_id=subtitle_id)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(json.loads(response.body), record)
            self.assertEqual(response.headers["location"], f"/tasks/{TASK}/edl/{REVISION}/exports/mlt/{EXPORT}")
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_get_and_verified_download_types(self):
        saved = {"export": {"export_id": str(EXPORT)}}
        with patch.object(editing.mlt_exporting_service, "load_task_mlt_export", return_value=saved) as load:
            response = editing.get_mlt_export(TASK, REVISION, EXPORT)
        load.assert_called_once_with(str(TASK), str(REVISION), str(EXPORT))
        self.assertEqual(json.loads(response.body), saved)
        for name, media_type in (("project.mlt", "application/xml"), ("source.edl.json", "application/json"),
                                 ("captions.ass", "text/x-ssa"), ("captions.srt", "application/x-subrip")):
            @contextmanager
            def resource():
                with io.BytesIO(b"verified export") as stream:
                    yield stream
            with patch.object(editing.mlt_exporting_service, "open_mlt_export_file", return_value=resource()) as opened:
                response = editing.download_mlt_export_file(TASK, REVISION, EXPORT, name)
            opened.assert_called_once_with(str(TASK), str(REVISION), str(EXPORT), name)
            self.assertEqual(response.media_type, media_type)
            self.assertEqual(b"".join(response.iter_bytes()), b"verified export")
            self.assertTrue(response.incoming.closed)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_safe_errors_and_unknown_failure(self):
        operations = (
            ("create_task_mlt_export", lambda: editing.create_mlt_export(TASK, REVISION)),
            ("create_task_mlt_export", lambda: editing.create_subtitled_mlt_export(TASK, REVISION, SUBTITLE)),
            ("load_task_mlt_export", lambda: editing.get_mlt_export(TASK, REVISION, EXPORT)),
            ("open_mlt_export_file", lambda: editing.download_mlt_export_file(TASK, REVISION, EXPORT, "project.mlt")),
        )
        for name, call in operations:
            with patch.object(editing.mlt_exporting_service, name, side_effect=ProjectError(409, "安全错误。")):
                with self.assertRaises(HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 409)
            with patch.object(editing.mlt_exporting_service, name, side_effect=RuntimeError("private path")), \
                    patch.object(editing.logger, "exception"):
                with self.assertRaises(HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 500)
                self.assertNotIn("private", caught.exception.detail)

    def test_openapi_no_body_geometry_or_font_knobs_and_uuid_ids(self):
        paths = app.openapi()["paths"]
        for suffix, method in (("/exports/mlt", "post"), ("/subtitles/{subtitle_id}/exports/mlt", "post"),
                               ("/exports/mlt/{export_id}", "get"),
                               ("/exports/mlt/{export_id}/files/{filename}", "get")):
            operation = paths["/tasks/{task_id}/edl/{revision_id}" + suffix][method]
            self.assertNotIn("requestBody", operation)
            parameters = {p["name"]: p for p in operation["parameters"]}
            self.assertTrue(all(p["in"] == "path" for p in parameters.values()))
            for name in set(parameters) - {"filename"}:
                self.assertEqual(parameters[name]["schema"]["format"], "uuid")


if __name__ == "__main__":
    unittest.main()
