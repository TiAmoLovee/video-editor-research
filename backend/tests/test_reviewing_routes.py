"""Strict JSON contract and safe route responses without platform TestClient loops."""

import json
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend.tests import test_editing_service as fixtures
from clipforge.routes import editing as editing_routes
from clipforge.routes import reviewing as routes
from clipforge.services import editing, reviewing


class ReviewingRouteTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.payload = {"candidate_id": self.selected[0], "decision": "accepted", "actor": " 王同学 ",
                        "reason": " 清晰 ", "request_id": str(uuid4())}

    def model(self, payload=None):
        # Validate as actual JSON to exercise strict UUID/string behavior seen by HTTP.
        return routes.ReviewRequest.model_validate_json(json.dumps(payload or self.payload))

    def test_create_read_recover_and_download_share_exact_audit_evidence(self):
        task_id = UUID(self.task_id)
        state = json.loads(routes.get_reviews(task_id).body)
        self.payload["expected_snapshot_sha256"] = state["candidate_snapshot_sha256"]
        response = routes.create_review(task_id, self.model())
        event = json.loads(response.body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["location"], f"/tasks/{task_id}/reviews/requests/{self.payload['request_id']}")
        self.assertEqual(event["actor"], "王同学")
        self.assertEqual(event["reason"], "清晰")
        self.assertEqual(json.loads(routes.get_review_request(task_id, UUID(event["request_id"])).body), event)
        audit = json.loads(routes.get_audit(task_id).body)
        download = routes.download_audit(task_id)
        self.assertEqual(json.loads(download.body), audit)
        self.assertIn("attachment;", download.headers["content-disposition"])
        self.assertTrue(download.headers["content-type"].startswith("application/json"))
        self.assertEqual(audit["events"], [event])

    def test_strict_review_model_rejects_missing_unknown_or_coerced_fields(self):
        invalid = [{"actor": " "}, {"actor": 1}, {"actor": None}, {"actor": "x" * 81},
                   {"decision": "pending"}, {"decision": True}, {"candidate_id": " "},
                   {"candidate_id": False}, {"reason": True}, {"reason": "x" * 2001},
                   {"request_id": "../request"}, {"request_id": 123}, {"unknown": "value"},
                   {"expected_snapshot_sha256": "a" * 63}]
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.model({**self.payload, **overrides})
        for required in ("candidate_id", "decision", "actor", "request_id"):
            value = {key: data for key, data in self.payload.items() if key != required}
            with self.subTest(missing=required), self.assertRaises(ValidationError):
                self.model(value)

    def test_edit_and_initial_edl_actor_are_optional_for_legacy_and_validated_when_supplied(self):
        raw = {"clips": [{"candidate_id": "c1", "source_in_frame": 1, "source_out_frame": 10}]}
        self.assertIsNone(editing_routes.SaveEditRequest.model_validate(raw).actor)
        named = editing_routes.SaveEditRequest.model_validate({**raw, "actor": " 张同学 "})
        self.assertEqual(named.actor, "张同学")
        initial = editing_routes.CreateEdlRequest.model_validate({"selected_ids": ["c1"], "actor": " 张同学 "})
        self.assertEqual(initial.actor, "张同学")
        for actor in (" ", "x" * 81, False, "张\n同学"):
            with self.subTest(actor=actor), self.assertRaises(ValidationError):
                editing_routes.SaveEditRequest.model_validate({**raw, "actor": actor})
            with self.subTest(initial_actor=actor), self.assertRaises(ValidationError):
                editing_routes.CreateEdlRequest.model_validate({"selected_ids": ["c1"], "actor": actor})
        revision = str(uuid4())
        with patch.object(editing_routes.editing_changes_service, "create_task_edit", return_value={"revision_id": revision}) as service:
            editing_routes.save_edl_edit(UUID(self.task_id), UUID(str(uuid4())), named)
        self.assertEqual(service.call_args.kwargs["actor"], "张同学")
        with patch.object(editing, "create_task_edl", return_value={"revision_id": revision}) as service:
            editing_routes.create_edl(UUID(self.task_id), initial)
        self.assertEqual(service.call_args.kwargs["actor"], "张同学")

    def test_safe_error_boundary_and_request_not_found(self):
        with self.assertRaises(HTTPException) as caught:
            routes.get_review_request(UUID(self.task_id), uuid4())
        self.assertEqual(caught.exception.status_code, 404)
        with patch.object(reviewing, "create_review", side_effect=editing.ProjectError(409, "候选快照已改变。")):
            with self.assertRaises(HTTPException) as caught:
                routes.create_review(UUID(self.task_id), self.model())
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (409, "候选快照已改变。"))
        with patch.object(reviewing, "review_state", side_effect=RuntimeError("private DB path")), \
                patch.object(editing_routes.logger, "exception"):
            with self.assertRaises(HTTPException) as caught:
                routes.get_reviews(UUID(self.task_id))
        self.assertEqual(caught.exception.status_code, 500)
        self.assertNotIn("private", caught.exception.detail)

    def test_registered_paths_and_openapi_require_human_name_and_request_id(self):
        app = FastAPI()
        app.include_router(routes.router)
        schema = app.openapi()
        self.assertEqual(set(schema["paths"]), {"/tasks/{task_id}/reviews", "/tasks/{task_id}/reviews/requests/{request_id}",
                                               "/tasks/{task_id}/audit", "/tasks/{task_id}/audit/download"})
        model = schema["components"]["schemas"]["ReviewRequest"]
        self.assertEqual(set(model["required"]), {"candidate_id", "decision", "actor", "request_id"})
        self.assertFalse(model["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
