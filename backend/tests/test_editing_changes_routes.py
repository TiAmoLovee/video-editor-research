"""Editing request and response contracts tested without HTTP or real services."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from clipforge.routes import editing
from clipforge.services.editing import ProjectError


def payload_fixture():
    return {"clips": [
        {"candidate_id": "c2", "source_in_frame": 30, "source_out_frame": 70},
        {"candidate_id": "c1", "source_in_frame": 10, "source_out_frame": 20},
    ], "editing_elapsed_ms": 12345}


class SaveEditRequestTests(unittest.TestCase):
    def test_valid_request_preserves_order_and_optional_elapsed(self):
        payload = payload_fixture()
        model = editing.SaveEditRequest.model_validate(payload)
        self.assertEqual(model.model_dump(exclude={"actor"}), payload)
        self.assertIsNone(model.actor)
        payload.pop("editing_elapsed_ms")
        self.assertIsNone(editing.SaveEditRequest.model_validate(payload).editing_elapsed_ms)
        payload["editing_elapsed_ms"] = None
        self.assertIsNone(editing.SaveEditRequest.model_validate(payload).editing_elapsed_ms)
        for value in (0, 86_400_000):
            payload["editing_elapsed_ms"] = value
            self.assertEqual(editing.SaveEditRequest.model_validate(payload).editing_elapsed_ms, value)

    def test_frames_reject_bool_strings_floats_and_missing_values(self):
        for field in ("source_in_frame", "source_out_frame"):
            for value in (True, False, "10", 10.0, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    payload = payload_fixture()
                    payload["clips"][0][field] = value
                    editing.SaveEditRequest.model_validate(payload)
            with self.subTest(field=field, missing=True), self.assertRaises(ValidationError):
                payload = payload_fixture()
                del payload["clips"][0][field]
                editing.SaveEditRequest.model_validate(payload)

    def test_invalid_ranges_are_rejected(self):
        for start, end in ((-1, 10), (0, 0), (10, 10), (11, 10)):
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                payload = payload_fixture()
                payload["clips"][0].update(source_in_frame=start, source_out_frame=end)
                editing.SaveEditRequest.model_validate(payload)

    def test_ids_reject_empty_blank_duplicates_non_string_and_oversized(self):
        for value in ("", " ", "\t\n", True, 1, "x" * 201):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                payload = payload_fixture()
                payload["clips"][0]["candidate_id"] = value
                editing.SaveEditRequest.model_validate(payload)
        payload = payload_fixture()
        payload["clips"][1]["candidate_id"] = payload["clips"][0]["candidate_id"]
        with self.assertRaises(ValidationError):
            editing.SaveEditRequest.model_validate(payload)

    def test_clip_list_limits_are_exact_and_no_tuple_coercion_occurs(self):
        one = payload_fixture()["clips"][0]
        for clips in ([], tuple(payload_fixture()["clips"]), None,
                      [{**one, "candidate_id": f"c{index}"} for index in range(101)]):
            with self.subTest(count=len(clips) if clips is not None else None), self.assertRaises(ValidationError):
                editing.SaveEditRequest.model_validate({"clips": clips})
        clips = [{**one, "candidate_id": f"c{index}"} for index in range(100)]
        self.assertEqual(len(editing.SaveEditRequest.model_validate({"clips": clips}).clips), 100)

    def test_elapsed_rejects_coercion_nonfinite_negative_and_over_limit(self):
        for value in (True, False, "123", 123.0, float("nan"), float("inf"), -1, 86_400_001):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                payload = payload_fixture()
                payload["editing_elapsed_ms"] = value
                editing.SaveEditRequest.model_validate(payload)

    def test_unknown_top_level_and_clip_fields_are_rejected(self):
        for target, field in (("request", "active_editing_seconds"),
                              ("clip", "source_id"), ("clip", "provenance")):
            with self.subTest(target=target, field=field), self.assertRaises(ValidationError):
                payload = payload_fixture()
                if target == "request":
                    payload[field] = 1
                else:
                    payload["clips"][0][field] = "untrusted"
                editing.SaveEditRequest.model_validate(payload)


class SaveEdlEditRouteTests(unittest.TestCase):
    task_id = UUID("c8f064b7-99ed-431a-8cc9-eae2e5a34b9a")
    parent_id = UUID("02880d39-5334-4d64-853e-b89bf64fba2a")
    new_id = UUID("77d62cc7-b8fe-40bf-93c0-fbdb837aefb1")

    def test_direct_route_passes_exact_clips_and_returns_created_revision(self):
        raw = payload_fixture()
        payload = editing.SaveEditRequest.model_validate(raw)
        record = {"revision_id": self.new_id, "parent_revision_id": str(self.parent_id),
                  "edit_effort": {"net_boundary_adjustment_seconds": 1.5}}
        before = deepcopy((raw, record))
        with patch.object(editing.editing_changes_service, "create_task_edit", return_value=record) as service:
            response = editing.save_edl_edit(self.task_id, self.parent_id, payload)
        service.assert_called_once_with(str(self.task_id), str(self.parent_id), raw["clips"],
                                        editing_elapsed_ms=raw["editing_elapsed_ms"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["location"], f"/tasks/{self.task_id}/edl/{self.new_id}")
        self.assertEqual(json.loads(response.body), {**record, "revision_id": str(self.new_id)})
        self.assertEqual((raw, record), before)

    def test_unknown_elapsed_is_forwarded_as_none_without_estimation(self):
        raw = payload_fixture()
        del raw["editing_elapsed_ms"]
        with patch.object(editing.editing_changes_service, "create_task_edit",
                          return_value={"revision_id": str(self.new_id)}) as service:
            editing.save_edl_edit(self.task_id, self.parent_id, editing.SaveEditRequest.model_validate(raw))
        service.assert_called_once_with(str(self.task_id), str(self.parent_id), raw["clips"],
                                        editing_elapsed_ms=None)

    def test_project_error_preserves_only_deliberate_safe_status_and_detail(self):
        payload = editing.SaveEditRequest.model_validate(payload_fixture())
        for status, detail in ((404, "任务不存在。"), (409, "来源记录不一致。"),
                               (422, "片段范围无效。"), (503, "保存服务暂不可用。")):
            with self.subTest(status=status), patch.object(
                    editing.editing_changes_service, "create_task_edit", side_effect=ProjectError(status, detail)):
                with self.assertRaises(HTTPException) as raised:
                    editing.save_edl_edit(self.task_id, self.parent_id, payload)
            self.assertEqual((raised.exception.status_code, raised.exception.detail), (status, detail))
            self.assertIsNone(raised.exception.__cause__)
            self.assertTrue(raised.exception.__suppress_context__)

    def test_unexpected_failure_does_not_expose_private_exception_message(self):
        payload = editing.SaveEditRequest.model_validate(payload_fixture())
        private_detail = "private local path / secret provider detail"
        with patch.object(editing.editing_changes_service, "create_task_edit",
                          side_effect=RuntimeError(private_detail)), patch.object(editing.logger, "exception"):
            with self.assertRaises(HTTPException) as raised:
                editing.save_edl_edit(self.task_id, self.parent_id, payload)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "剪辑清单服务暂不可用，请检查后台日志。")
        self.assertNotIn(private_detail, str(raised.exception.detail))

    def test_invalid_service_revision_cannot_produce_a_location_header(self):
        payload = editing.SaveEditRequest.model_validate(payload_fixture())
        with patch.object(editing.editing_changes_service, "create_task_edit",
                          return_value={"revision_id": "../../private"}), patch.object(editing.logger, "exception"):
            with self.assertRaises(HTTPException) as raised:
                editing.save_edl_edit(self.task_id, self.parent_id, payload)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "剪辑清单服务暂不可用，请检查后台日志。")


if __name__ == "__main__":
    unittest.main()
