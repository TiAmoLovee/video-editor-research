"""Render endpoints consume saved EDL versions, with no client or FFmpeg process."""

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

TASK_ID = "00000000-0000-4000-8000-000000000001"
REVISION_ID = "00000000-0000-4000-8000-000000000002"
RENDER_ID = "00000000-0000-4000-8000-000000000003"
TASK, REVISION, RENDER = map(UUID, (TASK_ID, REVISION_ID, RENDER_ID))
ROOT = f"/tasks/{TASK_ID}/edl/{REVISION_ID}/renders/{RENDER_ID}"


class RenderRoutesTests(unittest.TestCase):
    def test_create_returns_metadata_and_revision_specific_location(self):
        metadata = {"task_id": TASK_ID, "revision_id": REVISION_ID, "render_id": RENDER_ID,
                    "edl_file_sha256": "a" * 64, "edl_content_sha256": "b" * 64,
                    "profile": {"fps": {"num": 30, "den": 1}}, "total_frames": 33,
                    "output_bytes": 1000, "files": {"output.mp4": "c" * 64}}
        with patch.object(editing.rendering_service, "create_task_render", return_value=metadata) as create:
            with patch.object(editing.editing_service, "create_task_edl") as import_candidates:
                response = editing.create_render(TASK, REVISION)
        create.assert_called_once_with(TASK_ID, REVISION_ID, timeout=90)
        import_candidates.assert_not_called()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["location"], ROOT)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(json.loads(response.body), metadata)

    def test_get_returns_saved_render_and_verification(self):
        saved = {"render": {"render_id": RENDER_ID}, "verification": {"total_frames": 33}}
        with patch.object(editing.rendering_service, "load_task_render", return_value=saved) as load:
            response = editing.get_render(TASK, REVISION, RENDER)
        load.assert_called_once_with(TASK_ID, REVISION_ID, RENDER_ID)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), saved)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_download_streams_the_held_verified_handle_and_closes_it(self):
        for filename, media_type in (("output.mp4", "video/mp4"),
                                     ("verification.json", "application/json"),
                                     ("source.edl.json", "application/json")):
            with self.subTest(filename=filename):
                incoming = io.BytesIO(b"saved artifact")
                closed = []

                @contextmanager
                def resource():
                    try:
                        yield incoming
                    finally:
                        closed.append(True)
                        incoming.close()

                with patch.object(editing.rendering_service, "open_render_file", return_value=resource()) as opened:
                    response = editing.download_render_file(TASK, REVISION, RENDER, filename)
                opened.assert_called_once_with(TASK_ID, REVISION_ID, RENDER_ID, filename)
                self.assertEqual(response.media_type, media_type)
                self.assertEqual(response.headers["content-length"], str(len(b"saved artifact")))
                self.assertEqual(response.headers["content-disposition"], f'attachment; filename="{filename}"')
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(b"".join(response.iter_bytes()), b"saved artifact")
                response.close()
                self.assertEqual(closed, [True])
                self.assertTrue(incoming.closed)

    def test_download_rejects_service_disallowed_filename_before_response(self):
        @contextmanager
        def rejected():
            raise ProjectError(404, "此成片没有对应的下载文件。")
            yield

        with patch.object(editing.rendering_service, "open_render_file", return_value=rejected()) as opened:
            with self.assertRaises(HTTPException) as caught:
                editing.download_render_file(TASK, REVISION, RENDER, "project.mlt")
        opened.assert_called_once_with(TASK_ID, REVISION_ID, RENDER_ID, "project.mlt")
        self.assertEqual(caught.exception.status_code, 404)

    def test_service_errors_keep_safe_status_and_detail(self):
        operations = (
            ("create_task_render", lambda: editing.create_render(TASK, REVISION)),
            ("load_task_render", lambda: editing.get_render(TASK, REVISION, RENDER)),
            ("open_render_file", lambda: editing.download_render_file(TASK, REVISION, RENDER, "output.mp4")),
        )
        for name, call in operations:
            for status in (404, 409, 503):
                with self.subTest(route=name, status=status):
                    with patch.object(editing.rendering_service, name, side_effect=ProjectError(status, "安全错误。")):
                        with self.assertRaises(HTTPException) as caught:
                            call()
                    self.assertEqual(caught.exception.status_code, status)
                    self.assertEqual(caught.exception.detail, "安全错误。")

    def test_unknown_errors_do_not_expose_paths_or_tool_output(self):
        operations = (
            ("create_task_render", lambda: editing.create_render(TASK, REVISION)),
            ("load_task_render", lambda: editing.get_render(TASK, REVISION, RENDER)),
            ("open_render_file", lambda: editing.download_render_file(TASK, REVISION, RENDER, "output.mp4")),
        )
        for name, call in operations:
            with self.subTest(route=name):
                with patch.object(editing.rendering_service, name, side_effect=RuntimeError("PRIVATE C:/private/media")):
                    with patch.object(editing.logger, "exception"):
                        with self.assertRaises(HTTPException) as caught:
                            call()
                self.assertEqual(caught.exception.status_code, 500)
                self.assertNotIn("PRIVATE", caught.exception.detail)
                self.assertNotIn("C:/private", caught.exception.detail)

    def test_bad_render_identity_is_a_safe_failure(self):
        with patch.object(editing.rendering_service, "create_task_render", return_value={"render_id": "private"}):
            with patch.object(editing.logger, "exception"):
                with self.assertRaises(HTTPException) as caught:
                    editing.create_render(TASK, REVISION)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertNotIn("private", caught.exception.detail)

    def test_openapi_has_no_request_body_or_render_knobs(self):
        paths = app.openapi()["paths"]
        root = "/tasks/{task_id}/edl/{revision_id}/renders"
        post = paths[root]["post"]
        self.assertNotIn("requestBody", post)
        self.assertEqual({item["name"] for item in post["parameters"]}, {"task_id", "revision_id"})
        self.assertIn("201", post["responses"])
        self.assertIn("422", post["responses"])
        self.assertIn("90", post["description"])
        self.assertIn("共享", post["description"])
        for path, method, uuid_count in ((root, "post", 2),
                                         (root + "/{render_id}", "get", 3),
                                         (root + "/{render_id}/files/{filename}", "get", 3)):
            operation = paths[path][method]
            uuid_params = [item for item in operation["parameters"] if item["name"] != "filename"]
            self.assertEqual(len(uuid_params), uuid_count)
            self.assertTrue(all(item["in"] == "path" and item["schema"]["format"] == "uuid"
                                for item in uuid_params))
            self.assertIn("422", operation["responses"])


if __name__ == "__main__":
    unittest.main()
