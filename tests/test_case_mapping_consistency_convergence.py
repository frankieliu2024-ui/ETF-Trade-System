import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import check_system_consistency as consistency
from scripts import notification_center
from scripts import process_state_sync_request as state_sync


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


    def test_legacy_explicit_single_case_without_event_is_accepted(self):
        self.assertEqual(consistency._explicit_index_case_ids(
            "CASE-20260713-01 初始组合建立"
        ), ["CASE-20260713-01"])

    def test_legacy_missing_or_multiple_case_fails_closed(self):
        self.assertEqual(consistency._explicit_index_case_ids("待补充"), [])
        self.assertEqual(consistency._explicit_index_case_ids(
            "CASE-20260713-01；CASE-20260716-01"
        ), ["CASE-20260713-01", "CASE-20260716-01"])

    def test_event_backed_index_conflict_is_not_overridden_by_index(self):
        event = {"event_id": "trade-1", "code": "518880", "linked_decision_id": "decision-1"}
        mappings = {"trade-1": [{"decision_id": "decision-1", "case_id": "CASE-20260903-01", "security_code": "518880"}]}
        self.assertIsNone(consistency._validate_canonical_case_mapping(event, mappings))


    def _run_historical_mapping(self, row, *, current_date="2026-09-04", node="close", phase="POST_CLOSE_GRACE", trade_events=None, reviews=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ETF市场行情档案_2026.md").write_text("", encoding="utf-8")
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "共1笔证券交易：ETF 1笔、个股0笔\n"
                + row + "\n"
                "### 2.2 银证转账与非交易现金流水\n"
                encoding="utf-8",
            )
            state = root / "data" / "state"
            state.mkdir(parents=True)
            (state / "CURRENT.json").write_text(json.dumps({
                "market_date": current_date,
                "latest_valid_node": node,
                "data_freshness": {"market_phase": phase},
            }), encoding="utf-8")
            for rel, payload in (trade_events or []):
                path = root / "events" / "trades" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
            for rel, payload in (reviews or []):
                path = root / "events" / "reviews" / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(consistency, "ROOT", root):
                report = {"errors": [], "warnings": [], "checks": []}
                consistency._validate_historical_trade_case_mapping(report)
                return report

    def test_legacy_index_row_with_one_case_passes_full_validator(self):
        report = self._run_historical_mapping(
            "|2026-07-13 09:36:14|测试ETF|159941|买入|1|1|1|0|1|CASE-20260713-01 初始组合建立|"
        )
        self.assertEqual(report["checks"][-1]["status"], "PASS")
        self.assertEqual(report["errors"], [])

    def test_legacy_index_row_without_case_fails_full_validator(self):
        report = self._run_historical_mapping(
            "|2026-07-13 09:36:14|测试ETF|159941|买入|1|1|1|0|1|待补充|"
        )
        self.assertIn("historical_trade_case_mapping:2026-07-13 09:36:14:159941:case_count=0", report["errors"])

    def test_legacy_index_row_with_multiple_cases_fails_full_validator(self):
        report = self._run_historical_mapping(
            "|2026-07-13 09:36:14|测试ETF|159941|买入|1|1|1|0|1|CASE-20260713-01；CASE-20260716-01|"
        )
        self.assertIn("historical_trade_case_mapping:2026-07-13 09:36:14:159941:case_count=2", report["errors"])

    def test_post_boundary_row_without_event_fails_even_with_index_case(self):
        report = self._run_historical_mapping(
            "|2026-08-27 10:08:43|测试ETF|515880|买入|1|1|1|0|1|CASE-20260827-01|"
        )
        self.assertIn("historical_trade_case_mapping:2026-08-27 10:08:43:515880:missing_formal_trade_event", report["errors"])

    def test_event_backed_index_case_conflict_fails_full_validator(self):
        event_id = "20260903_112104_518880_trial_execution"
        report = self._run_historical_mapping(
            "|2026-09-03 11:21:20|测试ETF|518880|买入|1|1|1|0|1|CASE-20260902-01|",
            trade_events=[(f"{event_id}.json", {
                "event_id": event_id,
                "confirmed_at_beijing": "2026-09-03T11:21:20+08:00",
                "code": "518880",
            })],
            reviews=[("2026-09-03.json", {
                "market_date": "2026-09-03",
                "review": {
                    "case_id": "CASE-20260903-01",
                    "main_candidate": "黄金ETF（518880）",
                },
            })],
        )
        self.assertIn("historical_trade_case_mapping:2026-09-03 11:21:20:518880:index_case_conflict=CASE-20260902-01 canonical=CASE-20260903-01", report["errors"])

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


    def test_issue_268_real_2026_08_25_rows_retain_existing_case_owner_on_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-|-|-|-|-|-|-|\n"
                "|2026-08-25 10:02:57|通信ETF（513180）|513180|卖出|8,200|0.574|4,706.80|5.00|4,701.80|关联决策2026-08-25_207443bb40c2；真实成交已执行| <!-- TRADE_EVENT:20260825_100518 -->\n"
                "|2026-08-25 11:22:34|半导体ETF（561980）|561980|卖出|13,000|0.663|8,619.00|5.00|8,614.00|关联决策2026-08-25_7734ccf57b51；真实成交已执行| <!-- TRADE_EVENT:20260825_112516 -->\n"
                "### 2.2 银证转账与非交易现金流水\n"
                "### 2.12 CASE-20260817-01：通信ETF退出\n"
                "2026-08-25 10:02:57 通信ETF 513180 卖出 8200 0.574\n"
                "### 2.13 CASE-20260818-01：半导体ETF降风险\n"
                "2026-08-25 11:22:34 半导体ETF 561980 卖出 13000 0.663\n",
                encoding="utf-8",
            )
            with patch.object(state_sync, "ROOT", root), \
                 patch.object(state_sync, "EXPERIENCE", experience):
                for event_id, code, qty, price, decision in (
                    ("20260825_100518", "513180", 8200, 0.574, "2026-08-25_207443bb40c2"),
                    ("20260825_112516", "561980", 13000, 0.663, "2026-08-25_7734ccf57b51"),
                ):
                    state_sync.sync_experience_transaction_index({
                        "event_id": event_id,
                        "confirmed_at_beijing": f"{event_id[:4]}-{event_id[4:6]}-{event_id[6:8]}T{event_id[9:11]}:{event_id[11:13]}:{event_id[13:15]}+08:00",
                        "name": "通信ETF" if code == "513180" else "半导体ETF",
                        "code": code,
                        "side": "SELL",
                        "quantity": qty,
                        "price": price,
                        "amount": price * qty,
                        "fee": 5,
                        "fee_status": "CONFIRMED",
                        "linked_decision_id": decision,
                    })
            updated = experience.read_text(encoding="utf-8")
            self.assertIn("CASE-20260817-01；关联决策2026-08-25_207443bb40c2", updated)
            self.assertIn("CASE-20260818-01；关联决策2026-08-25_7734ccf57b51", updated)
            self.assertEqual(updated.count("TRADE_EVENT:20260825_100518"), 1)
            self.assertEqual(updated.count("TRADE_EVENT:20260825_112516"), 1)
            report = self._run_historical_mapping(
                "|2026-08-25 10:02:57|通信ETF|513180|卖出|8200|0.574|4706.80|5.00|4701.80|CASE-20260817-01|"
            )
            self.assertEqual(report["errors"], [])

    def test_issue_268_missing_lifecycle_does_not_erase_case_or_create_pending_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-|-|-|-|-|-|-|\n"
                "|2026-08-25 10:02:57|通信ETF（513180）|513180|卖出|8,200|0.574|4,706.80|待确认|4,706.80（未含待确认费用）|CASE-20260817-01| <!-- TRADE_EVENT:legacy -->\n"
                "### 2.2 银证转账与非交易现金流水\n"
                "### 2.12 CASE-20260817-01：通信ETF退出\n"
                "2026-08-25 10:02:57 通信ETF 513180 卖出 8200 0.574\n",
                encoding="utf-8",
            )
            event = {
                "event_id": "legacy",
                "confirmed_at_beijing": "2026-08-25T10:02:57+08:00",
                "name": "通信ETF",
                "code": "513180",
                "side": "SELL",
                "quantity": 8200,
                "price": 0.574,
                "amount": 4706.8,
                "fee": 5,
                "fee_status": "CONFIRMED",
                "lifecycle": "待确认",
            }
            with patch.object(state_sync, "ROOT", root), \
                 patch.object(state_sync, "EXPERIENCE", experience):
                state_sync.sync_experience_transaction_index(event)
            line = next(x for x in experience.read_text(encoding="utf-8").splitlines() if "TRADE_EVENT:legacy" in x)
            self.assertIn("CASE-20260817-01", line)
            self.assertNotIn("待确认", line)
            self.assertNotIn("未提供", line)



if __name__ == "__main__":
    unittest.main()
