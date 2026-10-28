from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notification_center as center
import notification_materiality_guard as guard


class FormalDecisionNotificationCanaryTests(unittest.TestCase):
    def test_scheduled_task_reports_are_always_notification_eligible(self):
        for report_type in ("ETF_FORMAL_DECISION", "ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"):
            event = {
                "event_type": "REPORT_DELIVERY_REQUEST",
                "report_type": report_type,
                "task_id": report_type,
                "task_run_id": "run-1",
                "request_id": "run-1",
                "occurrence_id": "occ-1",
                "report_id": report_type + ":run-1",
                "content_hash": "hash",
                "content": "无交易、无资格变化，但本次任务仍须发送完整报告。",
            }
            self.assertEqual(guard.notification_evidence_error(event), "")

    def _event(self, decision_id: str, decision_time: str, code: str, name: str, status: str, risk: str, amount_action: str = "新增0元；现有持仓维持；现金保留。") -> dict:
        return {
            "event_type": "FORMAL_DECISION",
            "decision_id": decision_id,
            "decision_time_beijing": decision_time,
            "candidate_code": code,
            "candidate_name": name,
            "comparison_snapshot": {"as_of_beijing": decision_time},
            "formal_decision": {
                "decision_id": decision_id,
                "candidate_code": code,
                "candidate_name": name,
                "main_candidate": f"{name}（{code}）",
                "opportunity_status": status,
                "risk_permission": risk,
                "amount_action": amount_action,
                "decisive_reason": "Scheduled Actor canary fixture",
                "data_as_of_beijing": decision_time,
            },
        }

    def _write_events(self, root: Path, *events: dict) -> None:
        directory = root / "events" / "decisions"
        directory.mkdir(parents=True, exist_ok=True)
        for event in events:
            (directory / f"{event['decision_id']}.json").write_text(
                json.dumps(event, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def test_candidate_switch_builds_material_guarded_notification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prior = self._event(
                "d1",
                "2026-09-04T11:20:00+08:00",
                "518880",
                "黄金ETF",
                "观察机会",
                "允许Trial",
            )
            latest = self._event(
                "d2",
                "2026-09-04T11:30:00+08:00",
                "159326",
