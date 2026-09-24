"""接口逻辑测试：队列边界使用替身，不要求本机安装 Celery 或启动 Redis。"""

import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException, Response
from pydantic import ValidationError

import clipforge.api as api
from clipforge.queue.client import QueueUnavailable

TASK_ID = "00000000-0000-4000-8000-000000000001"


class ApiTests(unittest.TestCase):
    def test_submission_returns_id_and_location(self):
        response = Response()
        with patch("clipforge.api.submit_add", return_value=TASK_ID) as submit:
            result = api.create_demo_task(api.AddRequest(left=2, right=3), response)
        submit.assert_called_once_with(2, 3)
        self.assertEqual(result.task_id, TASK_ID)
        self.assertEqual(response.headers["location"], result.status_url)

    def test_submission_failure_is_503(self):
        with patch("clipforge.api.submit_add", side_effect=QueueUnavailable("private details")):
            with self.assertRaises(HTTPException) as caught:
                api.create_demo_task(api.AddRequest(), Response())
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("private details", caught.exception.detail)

    def test_lookup_failure_is_503(self):
        with patch("clipforge.api.read_result", side_effect=QueueUnavailable()):
            with self.assertRaises(HTTPException) as caught:
                api.get_demo_task(UUID(TASK_ID))
        self.assertEqual(caught.exception.status_code, 503)

    def test_result_is_returned(self):
        with patch("clipforge.api.read_result", return_value={"task_id": TASK_ID, "state": "SUCCESS", "result": 5}):
            result = api.get_demo_task(UUID(TASK_ID))
        self.assertEqual(result.result, 5)

    def test_pending_does_not_claim_task_exists(self):
        note = "编号可能不存在或已过期。"
        with patch("clipforge.api.read_result", return_value={"task_id": TASK_ID, "state": "PENDING", "note": note}):
            result = api.get_demo_task(UUID(TASK_ID))
        self.assertIsNone(result.result)
        self.assertEqual(result.note, note)

    def test_invalid_numbers_are_rejected(self):
        for value in (True, "2", 2.5, 1000001):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                api.AddRequest(left=value, right=3)

    def test_openapi_submission_documents_202(self):
        operation = api.app.openapi()["paths"]["/demo/tasks"]["post"]
        self.assertIn("202", operation["responses"])
        self.assertIn("503", operation["responses"])


if __name__ == "__main__":
    unittest.main()
