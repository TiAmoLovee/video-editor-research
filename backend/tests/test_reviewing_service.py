"""Persisted human decisions, recovery, isolation and transaction-order evidence."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.tests import test_editing_service as fixtures
from clipforge.services import editing, reviewing
from clipforge.storage import jobs


class ReviewingServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditingServiceTests.setUp(self)
        self.candidate_id = self.selected[0]

    def review(self, decision="accepted", *, request_id=None, task_id=None, **kwargs):
        return reviewing.create_review(task_id or self.task_id, self.candidate_id, decision,
                                       kwargs.pop("actor", " 王同学 "), request_id=request_id or str(uuid4()), **kwargs)

    def other_task(self):
        task_id = str(uuid4())
        folder = jobs.job_dir(task_id)
        folder.mkdir()
        for name in ("candidates.json", "analysis.json"):
            (folder / name).write_bytes((self.folder / name).read_bytes())
        jobs.create_job(task_id, "other.mp4", "source.mp4")
        jobs.claim_job(task_id)
        jobs.update_job(task_id, "SUCCEEDED", "done", 100, result=deepcopy(self.result))
        return task_id

    def test_default_state_and_decisions_do_not_change_materials_job_or_edl(self):
        before = {path.name: path.read_bytes() for path in self.folder.iterdir() if path.is_file()}
        job = deepcopy(jobs.get_job(self.task_id))
        state = reviewing.review_state(self.task_id)
        self.assertEqual(len(state["items"]), len(self.candidates["candidates"]))
        self.assertTrue(all(item["decision"] == "undecided" and item["actor"] is None for item in state["items"]))
        accepted = self.review(reason="  开头清楚  ", expected_snapshot_sha256=state["candidate_snapshot_sha256"])
        rejected = self.review("rejected", actor="李同学", reason="需要补上下文")
        self.assertEqual(accepted["actor"], "王同学")
        self.assertEqual(accepted["reason"], "开头清楚")
        self.assertEqual(rejected["before"], accepted["after"])
        self.assertEqual(rejected["after"]["decision"], "rejected")
        self.assertEqual(reviewing.review_request(self.task_id, accepted["request_id"]), accepted)
        self.assertEqual(jobs.get_job(self.task_id), job)
        self.assertTrue(all((self.folder / name).read_bytes() == value for name, value in before.items()))
        self.assertFalse((self.folder / "edits").exists())
        item = next(item for item in reviewing.review_state(self.task_id)["items"] if item["candidate_id"] == self.candidate_id)
        self.assertEqual(item, {"candidate_id": self.candidate_id, **rejected["after"]})
        self.assertEqual(reviewing.task_audit(self.task_id)["events"], [accepted, rejected])

    def test_restart_and_unknown_result_replay_recover_exact_event_once(self):
        request_id = str(uuid4())
        event = self.review(request_id=request_id)
        code = ("import json; from clipforge.services.reviewing import review_request; "
                f"print(json.dumps(review_request('{self.task_id}', '{request_id}')))" )
        result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, encoding="utf-8")
        self.assertEqual(json.loads(result.stdout), event)
        self.assertEqual(self.review(request_id=request_id), event)
        self.assertEqual(len(reviewing.task_audit(self.task_id)["events"]), 1)
        with self.assertRaises(editing.ProjectError) as caught:
            self.review("rejected", request_id=request_id)
        self.assertEqual(caught.exception.status_code, 409)
        # A successful old request can still be recovered when current artifacts
        # become unavailable; it never creates a fresh decision for a new input.
        (self.folder / "candidates.json").write_text("unavailable", encoding="utf-8")
        self.assertEqual(self.review(request_id=request_id), event)

    def test_task_isolation_and_missing_request(self):
        other = self.other_task()
        request_id = str(uuid4())
        first = self.review(request_id=request_id)
        second = self.review("rejected", task_id=other, request_id=request_id)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(second["before"]["decision"], "undecided")
        self.assertEqual(reviewing.task_audit(self.task_id)["events"], [first])
        self.assertEqual(reviewing.task_audit(other)["events"], [second])
        with self.assertRaises(editing.ProjectError) as caught:
            reviewing.review_request(other, str(uuid4()))
        self.assertEqual(caught.exception.status_code, 404)

    def test_invalid_candidate_actor_decision_reason_and_request_write_nothing(self):
        invalid = [
            {"candidate_id": "unknown"}, {"candidate_id": " "}, {"candidate_id": True},
            {"decision": "pending"}, {"decision": True}, {"actor": None}, {"actor": "  "},
            {"actor": "x" * 81}, {"actor": "王\n同学"}, {"actor": False},
            {"reason": 1}, {"reason": "x" * 2001}, {"request_id": "../wrong"},
            {"expected_snapshot_sha256": "a" * 63},
        ]
        for overrides in invalid:
            value = {"candidate_id": self.candidate_id, "decision": "accepted", "actor": "王同学",
                     "request_id": str(uuid4()), **overrides}
            with self.subTest(overrides=overrides), self.assertRaises(editing.ProjectError) as caught:
                reviewing.create_review(self.task_id, **value)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(reviewing.task_audit(self.task_id)["events"], [])
        with self.assertRaises(editing.ProjectError) as caught:
            self.review(task_id=str(uuid4()))
        self.assertEqual(caught.exception.status_code, 404)

    def test_snapshot_binding_rejects_stale_writes_and_never_carries_old_state(self):
        state = reviewing.review_state(self.task_id)
        first = self.review(expected_snapshot_sha256=state["candidate_snapshot_sha256"])
        (self.folder / "candidates.json").write_text(json.dumps(self.candidates, indent=2), encoding="utf-8")
        new_state = reviewing.review_state(self.task_id)
        self.assertNotEqual(new_state["candidate_snapshot_sha256"], state["candidate_snapshot_sha256"])
        self.assertTrue(all(item["decision"] == "undecided" for item in new_state["items"]))
        with self.assertRaises(editing.ProjectError) as caught:
            self.review("rejected", expected_snapshot_sha256=state["candidate_snapshot_sha256"])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(reviewing.task_audit(self.task_id)["events"], [first])

    def test_corrupt_and_escaping_candidate_snapshots_fail_before_append(self):
        unsafe_job = deepcopy(jobs.get_job(self.task_id))
        unsafe_job["result"]["files"]["candidates.json"] = "../../outside-candidates.json"
        with patch.object(editing, "get_job", return_value=unsafe_job):
            with self.assertRaises(editing.ProjectError) as caught:
                self.review()
        self.assertEqual(caught.exception.status_code, 409)
        (self.folder / "candidates.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(editing.ProjectError) as caught:
            self.review()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(reviewing.task_audit(self.task_id)["events"], [])

    def test_concurrent_identical_requests_commit_one_event(self):
        request_id = str(uuid4())
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: self.review(request_id=request_id), range(12)))
        self.assertTrue(all(event == results[0] for event in results))
        self.assertEqual(len(reviewing.task_audit(self.task_id)["events"]), 1)

    def test_concurrent_different_decisions_keep_complete_before_after_chain(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda index: self.review(
                "accepted" if index % 2 else "rejected", actor=f"复核人{index}"), range(12)))
        self.assertEqual(len({event["event_id"] for event in results}), 12)
        events = reviewing.task_audit(self.task_id)["events"]
        before = reviewing._undecided()
        for event in events:
            self.assertEqual(event["before"], before)
            before = event["after"]
        state = next(item for item in reviewing.review_state(self.task_id)["items"] if item["candidate_id"] == self.candidate_id)
        self.assertEqual(state["request_id"], events[-1]["request_id"])

    def test_audit_rows_cannot_be_updated_or_deleted(self):
        self.review()
        with reviewing._database() as connection:
            for statement in ("UPDATE review_events SET event_json='{}'", "DELETE FROM review_events"):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)
                connection.rollback()
        self.assertEqual(len(reviewing.task_audit(self.task_id)["events"]), 1)


if __name__ == "__main__":
    unittest.main()
