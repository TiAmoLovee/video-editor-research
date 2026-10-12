"""Saved subtitle API contracts without unavailable local ASGI sockets."""

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

TASK, REVISION, SUBTITLE = map(UUID, (
    "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003"))


class SubtitleRoutesTests(unittest.TestCase):
    def test_create_and_get_saved_version(self):
        record = {"subtitle_id": str(SUBTITLE), "cue_count": 5}
        with patch.object(editing.subtitling_service, "create_task_subtitles", return_value=record) as create:
            response = editing.create_subtitles(TASK, REVISION)
        create.assert_called_once_with(str(TASK), str(REVISION))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.body), record)
        self.assertEqual(response.headers["location"], f"/tasks/{TASK}/edl/{REVISION}/subtitles/{SUBTITLE}")
        saved = {"subtitle": record, "plan": {"cues": []}}
        with patch.object(editing.subtitling_service, "load_task_subtitles", return_value=saved) as load:
            response = editing.get_subtitles(TASK, REVISION, SUBTITLE)
        load.assert_called_once_with(str(TASK), str(REVISION), str(SUBTITLE))
        self.assertEqual(json.loads(response.body), saved)

    def test_subtitle_download_keeps_verified_handle_and_media_type(self):
        for name, kind in (("captions.srt", "application/x-subrip"), ("captions.ass", "text/x-ssa"),
                           ("subtitles.json", "application/json")):
            @contextmanager
            def resource():
                with io.BytesIO(b"verified export") as stream:
                    yield stream
            with patch.object(editing.subtitling_service, "open_subtitle_file", return_value=resource()):
                response = editing.download_subtitle_file(TASK, REVISION, SUBTITLE, name)
            self.assertEqual(response.media_type, kind)
            self.assertEqual(b"".join(response.iter_bytes()), b"verified export")
            self.assertTrue(response.incoming.closed)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_render_binds_subtitle_id_without_client_font_parameters(self):
        rendered = {"render_id": str(SUBTITLE)}
        with patch.object(editing.rendering_service, "create_task_render", return_value=rendered) as create:
            response = editing.create_subtitled_render(TASK, REVISION, SUBTITLE)
        create.assert_called_once_with(str(TASK), str(REVISION), subtitle_id=str(SUBTITLE), timeout=90)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["location"], f"/tasks/{TASK}/edl/{REVISION}/renders/{SUBTITLE}")

    def test_safe_service_errors_and_unknown_failure(self):
        operations = (
            (editing.subtitling_service, "create_task_subtitles", lambda: editing.create_subtitles(TASK, REVISION)),
            (editing.subtitling_service, "load_task_subtitles", lambda: editing.get_subtitles(TASK, REVISION, SUBTITLE)),
            (editing.rendering_service, "create_task_render", lambda: editing.create_subtitled_render(TASK, REVISION, SUBTITLE)),
        )
        for service, name, call in operations:
            with patch.object(service, name, side_effect=ProjectError(409, "安全错误。")):
                with self.assertRaises(HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 409)
            with patch.object(service, name, side_effect=RuntimeError("private path")), patch.object(editing.logger, "exception"):
                with self.assertRaises(HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 500)
                self.assertNotIn("private", caught.exception.detail)

    def test_openapi_paths_registered_and_uuid_validated(self):
        paths = app.openapi()["paths"]
        for suffix, method in (("", "post"), ("/{subtitle_id}", "get"),
                               ("/{subtitle_id}/files/{filename}", "get"), ("/{subtitle_id}/renders", "post")):
            operation = paths["/tasks/{task_id}/edl/{revision_id}/subtitles" + suffix][method]
            parameters = {p["name"]: p for p in operation["parameters"]}
            self.assertEqual(parameters["task_id"]["schema"]["format"], "uuid")
            if "subtitle_id" in parameters:
                self.assertEqual(parameters["subtitle_id"]["schema"]["format"], "uuid")


if __name__ == "__main__":
    unittest.main()
