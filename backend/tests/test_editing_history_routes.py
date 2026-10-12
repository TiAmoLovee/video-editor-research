"""History HTTP contracts preserve safe errors and immutable readers."""

import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException

from clipforge.api import app
from clipforge.routes import editing
from clipforge.services.editing import ProjectError

TASK = UUID("00000000-0000-4000-8000-000000000001")
REVISION = UUID("00000000-0000-4000-8000-000000000002")


class EditingHistoryRoutesTests(unittest.TestCase):
    def test_revision_page_preserves_pagination_and_no_store(self):
        value = {"items": [], "total": 0, "offset": 2, "limit": 5, "has_more": False, "unavailable_count": 0}
        with patch.object(editing.editing_history_service, "list_task_edls", return_value=value) as reader:
            response = editing.list_edls(TASK, limit=5, offset=2)
        reader.assert_called_once_with(str(TASK), limit=5, offset=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), value)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_artifact_list_is_revision_bound(self):
        value = {"subtitles": [], "renders": [], "mlt_exports": [], "unavailable_count": 0}
        with patch.object(editing.editing_history_service, "list_revision_artifacts", return_value=value) as reader:
            response = editing.list_edl_artifacts(TASK, REVISION)
        reader.assert_called_once_with(str(TASK), str(REVISION))
        self.assertEqual(json.loads(response.body), value)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_service_and_unknown_errors_keep_safe_public_messages(self):
        calls = (("list_task_edls", lambda: editing.list_edls(TASK)),
                 ("list_revision_artifacts", lambda: editing.list_edl_artifacts(TASK, REVISION)))
        for name, call in calls:
            with self.subTest(name=name), patch.object(editing.editing_history_service, name,
                                                      side_effect=ProjectError(503, "安全错误。")):
                with self.assertRaises(HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 503)
                self.assertEqual(caught.exception.detail, "安全错误。")
            with patch.object(editing.editing_history_service, name, side_effect=RuntimeError("SECRET local path")), \
                    patch.object(editing.logger, "exception"):
                with self.assertRaises(HTTPException) as caught:
                    call()
                self.assertEqual(caught.exception.status_code, 500)
                self.assertNotIn("SECRET", caught.exception.detail)

    def test_openapi_retains_post_and_adds_bounded_query_and_uuid_gets(self):
        paths = app.openapi()["paths"]
        root = "/tasks/{task_id}/edl"
        self.assertIn("post", paths[root])
        operation = paths[root]["get"]
        params = {item["name"]: item for item in operation["parameters"]}
        self.assertEqual(params["task_id"]["schema"]["format"], "uuid")
        self.assertEqual(params["limit"]["schema"]["minimum"], 1)
        self.assertEqual(params["limit"]["schema"]["maximum"], 50)
        self.assertEqual(params["offset"]["schema"]["minimum"], 0)
        self.assertEqual(params["offset"]["schema"]["maximum"], 500)
        artifact = paths[root + "/{revision_id}/artifacts"]["get"]
        self.assertTrue(all(item["schema"]["format"] == "uuid" for item in artifact["parameters"]))


if __name__ == "__main__":
    unittest.main()
