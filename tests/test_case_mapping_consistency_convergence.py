import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import check_system_consistency as consistency
from scripts import notification_center


class CaseMappingConsistencyConvergenceTests(unittest.TestCase):
    def test_canonical_review_mapping_replaces_experience_routing_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = root / "events" / "reviews"
            trades = root / "events" / "trades"
            state = root / "data" / "state"
            reviews.mkdir(parents=True)
            trades.mkdir(parents=True)
            state.mkdir(parents=True)
            event_id = "20260903_112104_518880_trial_execution"
            (reviews / "2026-09-03.json").write_text(json.dumps({
                "review": {"case_mapping": {"primary": {
                    "trade_event_id": event_id,
                    "decision_id": event_id,
                    "case_id": "CASE-20260903-01",
                    "security_code": "518880",
                }}}
            }), encoding="utf-8")
            (trades / f"{event_id}.json").write_text(json.dumps({
                "event_id": event_id,
                "execution_status": "EXECUTED",
                "confirmed_at_beijing": "2026-09-03T11:21:04+08:00",
                "code": "518880",
                "linked_decision_id": event_id,
            }), encoding="utf-8")
            (root / "ETF市场行情档案_2026.md").write_text(f"{event_id}｜archive\n", encoding="utf-8")
            (root / "ETF交易复盘与经验库_2026.md").write_text(
                f"### 2.17 CASE-20260903-01：黄金ETF\nTRADE_EVENT:{event_id}\n",
                encoding="utf-8",
            )
            (state / "CURRENT.json").write_text(json.dumps({
                "market_date": "2026-09-04",
                "latest_valid_node": "close",
                "data_freshness": {"market_phase": "POST_CLOSE_GRACE"},
            }), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_trade_event_formal_sync(report)
            self.assertEqual(report["checks"][-1]["status"], "PASS")
            self.assertEqual(report["errors"], [])

    def test_missing_or_conflicting_canonical_mapping_fails_closed(self):
        event = {
            "event_id": "trade-1",
            "code": "518880",
            "linked_decision_id": "decision-1",
        }
        self.assertEqual(
            consistency._validate_canonical_case_mapping(event, {}),
            "formal_case_mapping_count=0",
        )
        self.assertEqual(
            consistency._validate_canonical_case_mapping(event, {
                "trade-1": [{
                    "decision_id": "decision-1",
                    "case_id": "CASE-1",
                    "security_code": "515880",
                }]
            }),
            "formal_case_mapping_security_code=515880",
        )

    def test_opportunity_titles_are_simplified_without_changing_status_logic(self):
        fixed = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
        previous = {
            "event_type": "FORMAL_DECISION",
            "decision_id": "old",
            "decision_time_beijing": "2026-09-03T09:50:00+00:00",
            "candidate_code": "515880",
            "candidate_name": "通信ETF",
            "formal_decision": {
                "opportunity_status": "观察机会",
                "risk_permission": "允许Trial",
            },
        }
        latest = {
            "event_type": "FORMAL_DECISION",
            "decision_id": "new",
            "decision_time_beijing": "2026-09-03T09:55:00+00:00",
            "candidate_code": "518880",
            "candidate_name": "黄金ETF",
            "formal_decision": {
                "opportunity_status": "Trial机会",
                "risk_permission": "允许Trial",
                "amount_action": "查看正式金额与失效条件后由用户人工决定。",
                "decisive_reason": "测试",
            },
        }
        with patch.object(notification_center, "_formal_decision_events", return_value=[
            {**previous, "_opportunity_status": "观察机会"},
            {**latest, "_opportunity_status": "Trial机会"},
        ]), patch.object(notification_center, "now", return_value=fixed):
            event = notification_center.formal_decision_change_event()
        self.assertEqual(event["title"], "【Trial机会】黄金ETF（518880）")
        self.assertIn("正式金额与失效条件", event["content"])

    def test_observation_title_is_simplified(self):
        self.assertIn('title = f"【观察机会】{target}"',
                      Path(notification_center.__file__).read_text(encoding="utf-8"))
        self.assertNotIn('title = f"【观察机会｜无需下单】{target}"',
                         Path(notification_center.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
