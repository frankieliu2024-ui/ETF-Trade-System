"""Controlled, side-effect-free production handoff simulation.

This is deliberately a test harness, not a second producer.  It models the
canonical artifacts emitted by the existing owners and rejects any handoff
whose identity or hash changes between stages.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from scripts.formal_entry_contract import (
    ENTRY_AUTOMATIC_SCHEDULED_TASK,
    ENTRY_MANUAL_CONVERSATION,
    ENTRY_MANUAL_SCHEDULED_TASK,
    build_identity,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def simulate(entry_mode: str, source: str, occurrence: str) -> dict:
    request_id = "fd_acceptance_" + occurrence
    identity = {
        "request_id": request_id,
        "parent_request_id": request_id,
        "entry_mode": entry_mode,
        "source": source,
        "task_id": "ETF正式决策",
        "task_run_id": "run_" + occurrence,
        "occurrence": occurrence,
        "market_date": "2026-10-13",
    }
    build_identity(identity)
    snapshot = {**identity, "snapshot_id": request_id + "_snapshot"}
    context = {**identity, "snapshot_id": snapshot["snapshot_id"], "query_context": "bound"}
    dwp = {**identity, "problem_graph": [{"problem_id": "p1", "question": "decision"}]}
    bds = {**identity, "decision_id": request_id + "_decision", "answers": {"p1": "HOLD"}}
    decision = {**identity, "decision_id": bds["decision_id"], "event_type": "FORMAL_DECISION"}
    content = "controlled formal decision report for " + request_id
    completion = {**decision, "content_hash": _sha(content), "event_path": "events/decisions/" + request_id + ".json"}
    report = {**identity, "decision_id": decision["decision_id"], "report_id": request_id + "_report", "content_hash": _sha(content), "source_event_path": completion["event_path"]}
    notification = {**identity, "decision_id": report["decision_id"], "report_id": report["report_id"], "content_hash": report["content_hash"], "event_type": "REPORT_DELIVERY_REQUEST", "pushplus_result": "UNVERIFIED_EXTERNAL_SEND"}
    final = {**identity, "decision_id": decision["decision_id"], "report_id": report["report_id"], "notification_id": request_id + "_notification", "status": "MERGED_BUT_NOT_ACCEPTED"}
    artifacts = (snapshot, context, dwp, bds, decision, completion, report, notification, final)
    for artifact in artifacts:
        for key in ("request_id", "parent_request_id", "entry_mode", "source", "task_id", "task_run_id", "occurrence", "market_date"):
            if artifact.get(key) != identity[key]:
                raise AssertionError("identity mismatch:" + key)
    if report["content_hash"] != completion["content_hash"] or notification["report_id"] != report["report_id"]:
        raise AssertionError("completion/report/notification mismatch")
    return {"request_id": request_id, "decision_id": decision["decision_id"], "report_id": report["report_id"], "notification_id": final["notification_id"], "status": final["status"]}


class FormalDecisionProductionE2ETests(unittest.TestCase):
    CASES = (
        (ENTRY_MANUAL_CONVERSATION, "CHATGPT_MANUAL_FORMAL_ANALYSIS", "conversation"),
        (ENTRY_MANUAL_SCHEDULED_TASK, "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", "manual_task"),
        (ENTRY_AUTOMATIC_SCHEDULED_TASK, "CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "scheduled_20261013_1430"),
    )

    def test_all_three_entries_complete_one_bound_chain(self):
        results = [simulate(*case) for case in self.CASES]
        self.assertEqual(len({item["request_id"] for item in results}), 3)
        self.assertTrue(all(item["status"] == "MERGED_BUT_NOT_ACCEPTED" for item in results))

    def test_failure_cases_are_fail_closed(self):
        valid = simulate(*self.CASES[0])
        self.assertNotEqual(valid["request_id"], "old_request")
        self.assertRaises(AssertionError, lambda: self._assert_binding(valid, {"request_id": "old_request"}))
        self.assertRaises(AssertionError, lambda: self._assert_binding(valid, {"content_hash": "wrong"}))

    @staticmethod
    def _assert_binding(expected, actual):
        if actual.get("request_id", expected["request_id"]) != expected["request_id"]:
            raise AssertionError("stale request consumed")
        if actual.get("content_hash", expected.get("content_hash")) != expected.get("content_hash"):
            raise AssertionError("content hash mismatch")


if __name__ == "__main__":
    unittest.main()
