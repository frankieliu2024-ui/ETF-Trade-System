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
                "电网设备ETF",
                "观察机会",
                "允许Trial",
            )
            self._write_events(root, prior, latest)
            with patch.object(center, "ROOT", root), patch.object(
                center,
                "now",
                return_value=datetime.fromisoformat("2026-09-04T11:31:00+08:00"),
            ):
                event = center.formal_decision_change_event()

            self.assertIsNotNone(event)
            self.assertEqual(event["event_type"], "FORMAL_DECISION_MATERIAL_CHANGE")
            self.assertEqual(event["related_decision_id"], "d2")
            self.assertEqual(event["security_code"], "159326")
            self.assertIn("【观察机会】", event["title"])
            self.assertEqual(event["confirmation_context"]["previous_security_code"], "518880")
            self.assertEqual(guard.notification_evidence_error(event), "")

    def test_no_material_change_stays_silent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prior = self._event(
                "d1",
                "2026-09-04T11:20:00+08:00",
                "159326",
                "电网设备ETF",
                "观察机会",
                "允许Trial",
            )
            latest = self._event(
                "d2",
                "2026-09-04T11:30:00+08:00",
                "159326",
                "电网设备ETF",
                "观察机会",
                "允许Trial",
                amount_action="新增0元；现金继续保留。",
            )
            self._write_events(root, prior, latest)
            with patch.object(center, "ROOT", root), patch.object(
                center,
                "now",
                return_value=datetime.fromisoformat("2026-09-04T11:31:00+08:00"),
            ):
                event = center.formal_decision_change_event()

            self.assertIsNone(event)

    def test_trial_upgrade_is_actionable_but_never_auto_executes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prior = self._event(
                "d1",
                "2026-09-04T11:20:00+08:00",
                "159326",
                "电网设备ETF",
                "观察机会",
                "允许Trial",
            )
            latest = self._event(
                "d2",
                "2026-09-04T11:30:00+08:00",
                "159326",
                "电网设备ETF",
                "Trial机会",
                "允许Trial",
                amount_action="Trial新增5,000元；由用户人工下单。",
            )
            self._write_events(root, prior, latest)
            with patch.object(center, "ROOT", root), patch.object(
                center,
                "now",
                return_value=datetime.fromisoformat("2026-09-04T11:31:00+08:00"),
            ):
                event = center.formal_decision_change_event()

            self.assertIsNotNone(event)
            self.assertIn("【Trial机会】", event["title"])
            self.assertIn("人工执行", event["user_action"])
            self.assertEqual(guard.notification_evidence_error(event), "")


if __name__ == "__main__":
    unittest.main()
