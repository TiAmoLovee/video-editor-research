"""Editing API boundaries without an HTTP client, media probes or a task DB."""

import json
from contextlib import contextmanager
import io
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from clipforge.api import app
from clipforge.routes import editing
from clipforge.services.editing import ProjectError

TASK_ID = "00000000-0000-4000-8000-000000000001"
REVISION_ID = "00000000-0000-4000-8000-000000000002"
TASK = UUID(TASK_ID)
REVISION = UUID(REVISION_ID)


@contextmanager
def held_bytes(content):
    with io.BytesIO(content) as incoming:
        yield incoming


class EditingRoutesTests(unittest.TestCase):
    def test_create_preserves_selection_and_returns_revision_location(self):
        metadata = {"task_id": TASK_ID, "revision_id": REVISION_ID, "clip_count": 2}
        with patch.object(editing.editing_service, "create_task_edl", return_value=metadata) as create:
            response = editing.create_edl(TASK, editing.CreateEdlRequest(selected_ids=["second", "first"]))
        create.assert_called_once_with(TASK_ID, ["second", "first"], timeout=90)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.body), metadata)
        self.assertEqual(response.headers["location"], f"/tasks/{TASK_ID}/edl/{REVISION_ID}")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_get_returns_saved_revision_edl_and_manifest(self):
        saved = {"revision": {"revision_id": REVISION_ID}, "edl": {"clips": []}, "manifest": {}}
        with patch.object(editing.editing_service, "load_task_edl", return_value=saved) as load:
            response = editing.get_edl(TASK, REVISION)
        load.assert_called_once_with(TASK_ID, REVISION_ID)
        self.assertEqual(json.loads(response.body), saved)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_file_download_uses_service_allowlist(self):
        with patch.object(editing.editing_service, "open_revision_file",
                          return_value=held_bytes(b'{"clips": []}')) as find:
            response = editing.download_edl_file(TASK, REVISION, "project.edl.json")
        find.assert_called_once_with(TASK_ID, REVISION_ID, "project.edl.json")
        self.assertEqual(b''.join(response.iter_bytes()), b'{"clips": []}')
        self.assertTrue(response.incoming.closed)
        self.assertEqual(response.media_type, "application/json")
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="project.edl.json"')
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_source_download_uses_hash_checking_service(self):
        with patch.object(editing.editing_service, "open_revision_source",
                          return_value=held_bytes(b'verified media')) as find:
            response = editing.download_edl_source(TASK, REVISION)
        find.assert_called_once_with(TASK_ID, REVISION_ID)
        self.assertEqual(b''.join(response.iter_bytes()), b'verified media')
        self.assertTrue(response.incoming.closed)
        self.assertEqual(response.media_type, "video/mp4")
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="normalized.mp4"')

    def test_service_errors_keep_safe_status_and_detail_on_every_route(self):
        operations = (
            ("create_task_edl", lambda: editing.create_edl(TASK, editing.CreateEdlRequest(selected_ids=["c1"]))),
            ("load_task_edl", lambda: editing.get_edl(TASK, REVISION)),
            ("open_revision_file", lambda: editing.download_edl_file(TASK, REVISION, "project.mlt")),
            ("open_revision_source", lambda: editing.download_edl_source(TASK, REVISION)),
        )
        for name, call in operations:
            with self.subTest(route=name):
                with patch.object(editing.editing_service, name, side_effect=ProjectError(409, "安全错误。")):
                    with self.assertRaises(HTTPException) as caught:
                        call()
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail, "安全错误。")

    def test_unexpected_errors_do_not_expose_internal_details_on_every_route(self):
        operations = (
            ("create_task_edl", lambda: editing.create_edl(TASK, editing.CreateEdlRequest(selected_ids=["c1"]))),
            ("load_task_edl", lambda: editing.get_edl(TASK, REVISION)),
            ("open_revision_file", lambda: editing.download_edl_file(TASK, REVISION, "analysis.json")),
            ("open_revision_source", lambda: editing.download_edl_source(TASK, REVISION)),
        )
        for name, call in operations:
            with self.subTest(route=name):
                with patch.object(editing.editing_service, name, side_effect=RuntimeError("SECRET C:/private/file")):
                    with patch.object(editing.logger, "exception") as log:
                        with self.assertRaises(HTTPException) as caught:
                            call()
                self.assertEqual(caught.exception.status_code, 500)
                self.assertNotIn("SECRET", caught.exception.detail)
                self.assertNotIn("C:/private", caught.exception.detail)
                log.assert_called_once()

    def test_malformed_service_response_is_also_a_safe_error(self):
        with patch.object(editing.editing_service, "create_task_edl", return_value={"revision_id": "private"}):
            with patch.object(editing.logger, "exception"):
                with self.assertRaises(HTTPException) as caught:
                    editing.create_edl(TASK, editing.CreateEdlRequest(selected_ids=["c1"]))
        self.assertEqual(caught.exception.status_code, 500)
        self.assertNotIn("private", caught.exception.detail)

    def test_ids_are_strict_nonblank_unique_and_bounded(self):
        invalid = ([], ["c"] * 101, ["x" * 201], [""], [" \t\n"], ["c", "c"],
                   [1], [True], [None], "c", ("c",), None)
        for ids in invalid:
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                editing.CreateEdlRequest(selected_ids=ids)

    def test_valid_limit_and_literal_order_are_preserved(self):
        ids = ["x" * 200] + [f"candidate-{index}" for index in range(99)]
        self.assertEqual(editing.CreateEdlRequest(selected_ids=ids).selected_ids, ids)
        self.assertEqual(editing.CreateEdlRequest(selected_ids=[" c "]).selected_ids, [" c "])

    def test_extra_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            editing.CreateEdlRequest(selected_ids=["c"], timeout=3600)

    def test_download_resources_close_if_consumption_stops(self):
        response = editing.VerifiedDownload(held_bytes(b'media'), 'normalized.mp4', 'video/mp4')
        self.assertFalse(response.incoming.closed)
        response.close()
        response.close()
        self.assertTrue(response.incoming.closed)

    def test_openapi_documents_routes_uuid_ids_and_validation(self):
        schema = app.openapi()
        paths = schema["paths"]
        root = "/tasks/{task_id}/edl"
        post = paths[root]["post"]
        self.assertIn("201", post["responses"])
        self.assertIn("422", post["responses"])
        self.assertIn("90", post["description"])
        for path in (root + "/{revision_id}", root + "/{revision_id}/files/{filename}",
                     root + "/{revision_id}/source"):
            operation = paths[path]["get"]
            self.assertIn("422", operation["responses"])
            uuid_params = [item for item in operation["parameters"] if item["name"] in {"task_id", "revision_id"}]
            self.assertEqual(len(uuid_params), 2)
            self.assertTrue(all(item["schema"]["format"] == "uuid" for item in uuid_params))
        model = schema["components"]["schemas"]["CreateEdlRequest"]
        self.assertFalse(model["additionalProperties"])
        ids = model["properties"]["selected_ids"]
        self.assertEqual((ids["minItems"], ids["maxItems"]), (1, 100))
        self.assertEqual(ids["items"]["maxLength"], 200)
        self.assertEqual(ids["items"]["type"], "string")

    def test_api_image_installs_ffprobe_provider_before_dropping_root(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "deploy" / "Dockerfile.api").read_text(encoding="utf-8")
        runtime = dockerfile.split("FROM python:", 1)[1]
        installation = re.search(r"apt-get install[^\n]*\bffmpeg\b", runtime)
        self.assertIsNotNone(installation)
        self.assertLess(installation.start(), runtime.index("USER appuser"))
        self.assertIn("/var/lib/apt/lists/*", runtime)


if __name__ == "__main__":
    unittest.main()
