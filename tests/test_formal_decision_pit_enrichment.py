import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import process_state_sync_request as sync


class FormalDecisionPitEnrichmentTests(unittest.TestCase):
    def _snapshot(self, root: Path, name: str, captured: str, provider_as_of: str, close: float = 1.342):
        path = root / "data" / "market" / "snapshots" / f"2026-09-09_{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "market_date": "2026-09-09",
            "quality_status": "PASS",
            "captured_at_beijing": captured,
            "provider_as_of": provider_as_of,
            "rows": [{
                "symbol": "515220", "close": close, "quality_status": "PASS",
                "as_of_beijing": provider_as_of, "change_pct": 3.07,
            }],
        }), encoding="utf-8")
        return str(path.relative_to(root)).replace("\\", "/")

    def test_provider_fact_and_capture_clocks_select_consumed_afternoon_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._snapshot(root, "112406", "2026-09-09T11:24:06+08:00", "2026-09-09T11:24:00+08:00", 1.342)
            afternoon = self._snapshot(root, "132424", "2026-09-09T13:24:24+08:00", "2026-09-09T13:24:21+08:00", 1.330)
            with patch.object(sync, "ROOT", root):
                selected, snap, status = sync.select_point_in_time_snapshot(
                    "2026-09-09", "2026-09-09T13:24:21+08:00",
                    availability_time="2026-09-09T13:25:00+08:00",
                    consumed_snapshot=afternoon,
                )
            self.assertEqual(selected, afternoon)
            self.assertEqual(snap["captured_at_beijing"], "2026-09-09T13:24:24+08:00")
            self.assertEqual(status, "CONSUMED_SNAPSHOT_VALIDATED")

    def test_two_clock_fallback_selects_latest_legal_snapshot_without_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._snapshot(root, "112406", "2026-09-09T11:24:06+08:00", "2026-09-09T11:24:00+08:00")
            afternoon = self._snapshot(
                root, "132424", "2026-09-09T13:24:24+08:00",
                "2026-09-09T13:24:21+08:00",
            )
            with patch.object(sync, "ROOT", root):
                selected, _, status = sync.select_point_in_time_snapshot(
                    "2026-09-09", "2026-09-09T13:24:21+08:00",
                    availability_time="2026-09-09T13:25:00+08:00",
                )
            self.assertEqual(selected, afternoon)
            self.assertEqual(status, "POINT_IN_TIME_SNAPSHOT_TWO_CLOCK_VALIDATED")

    def test_snapshot_after_persistence_boundary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            future = self._snapshot(root, "132424", "2026-09-09T13:24:24+08:00", "2026-09-09T13:24:21+08:00")
            with patch.object(sync, "ROOT", root):
                selected, _, status = sync.select_point_in_time_snapshot(
                    "2026-09-09", "2026-09-09T13:24:21+08:00",
                    availability_time="2026-09-09T13:24:23+08:00",
                    consumed_snapshot=future,
                )
            self.assertEqual((selected, status), ("", "SNAPSHOT_NOT_AVAILABLE_BY_PERSISTENCE_BOUNDARY"))

    def test_market_fact_after_decision_cutoff_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            future_fact = self._snapshot(root, "132424", "2026-09-09T13:24:24+08:00", "2026-09-09T13:24:22+08:00")
            with patch.object(sync, "ROOT", root):
                selected, _, status = sync.select_point_in_time_snapshot(
                    "2026-09-09", "2026-09-09T13:24:21+08:00",
                    availability_time="2026-09-09T13:25:00+08:00",
                    consumed_snapshot=future_fact,
                )
            self.assertEqual((selected, status), ("", "SNAPSHOT_MARKET_FACT_AFTER_DECISION_CUTOFF"))

    def test_legacy_capture_and_fact_before_cutoff_remains_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = self._snapshot(root, "103208", "2026-09-09T10:32:08+08:00", "2026-09-09T10:32:00+08:00")
            with patch.object(sync, "ROOT", root):
                selected, _, status = sync.select_point_in_time_snapshot(
                    "2026-09-09", "2026-09-09T10:33:00+08:00",
                    availability_time="2026-09-09T10:33:10+08:00",
                    consumed_snapshot=legacy,
                )
            self.assertEqual(selected, legacy)
            self.assertEqual(status, "CONSUMED_SNAPSHOT_VALIDATED")

    def test_no_legal_snapshot_preserves_fail_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._snapshot(root, "132424", "2026-09-09T13:24:24+08:00", "2026-09-09T13:24:22+08:00")
            with patch.object(sync, "ROOT", root):
                selected, snap, status = sync.select_point_in_time_snapshot(
                    "2026-09-09", "2026-09-09T13:24:21+08:00",
                    availability_time="2026-09-09T13:25:00+08:00",
                )
            self.assertEqual((selected, snap, status), ("", {}, "NO_PRIOR_SNAPSHOT"))

    def test_record_formal_decision_binds_price_and_comparison_to_same_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = self._snapshot(
                root, "132424", "2026-09-09T13:24:24+08:00",
                "2026-09-09T13:24:21+08:00", 1.330,
            )
            (root / "config" / "market").mkdir(parents=True, exist_ok=True)
            (root / "config" / "market" / "etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "515220", "name": "煤炭ETF"}]}),
                encoding="utf-8",
            )
            (root / "events" / "decisions").mkdir(parents=True)
            request = {
                "request_id": "decision-1325",
                "requested_at_beijing": "2026-09-09T13:25:00+08:00",
                "market_date": "2026-09-09",
                "consumed_snapshot": selected,
                "formal_decision": {
                    "decision_id": "decision-1325",
                    "data_as_of_beijing": "2026-09-09T13:24:21+08:00",
                    "candidate_code": "515220",
                    "candidate_name": "煤炭ETF",
                    "main_candidate": "煤炭ETF（515220）",
                    "opportunity_status": "Trial机会",
                    "lifecycle": "",
                    "observation_management": [{"action": "RETAIN", "code": "515220", "name": "煤炭ETF", "thscode": "515220.SH", "thesis": "PIT测试中的既有观察身份保持不变", "falsifier": "本测试不评估交易假设", "next_decision_information": "仅验证PIT绑定", "information_value_reason": "保持既有观察管理合同完整"}],
                    "capital_competition": {
                        "next_unit_capital_use": "保留现金",
                        "full_competition_completed": True,
                        "releasable_capital_reviewed": True,
                        "post_action_deployable_cash": 10000.0,
                        "future_opportunity_capacity": "仍可承载后续Trial/Confirm",
                        "concentration_account_structure_effect": "不增加集中度",
                        "selected_state_reason": "现金优于当前可执行候选",
                        "new_amount_yuan": 0,
                        "zero_amount_decisive_reason": "完整资本竞争后保留现金",
                        "compared_capital_states": [
                            {
                                "state_name": "维持现有组合+现金",
                                "capital_action": "不新增",
                                "remaining_deployable_cash": 10000.0,
                                "why_not_selected": "已选中"
                            },
                            {
                                "state_name": "候选Trial+剩余现金",
                                "capital_action": "新增5000元",
                                "remaining_deployable_cash": 5000.0,
                                "why_not_selected": "未被当前决策选中"
                            }
                        ],
                        "held_etf_add_capital_reviews": [],
                        "capital_release_migrations": [],
                        "etf_opportunity_reviews": [{"security_code": "515220", "category": "OBSERVED_ETF", "opportunity_status": "Trial机会", "conclusion": "继续观察", "reason": "本测试仅验证PIT绑定"}]
                    },
                },
            }
            with patch.object(sync, "ROOT", root):
                recorded, _ = sync.record_formal_decision(request)
            event = json.loads((root / "events" / "decisions" / "decision-1325.json").read_text(encoding="utf-8"))
            self.assertTrue(recorded)
            self.assertEqual(event["price_source_snapshot"], selected)
            self.assertEqual(event["comparison_snapshot"]["as_of_beijing"], "2026-09-09T13:24:24+08:00")
            self.assertEqual(event["price_at_decision"], 1.330)


    def test_explicit_consumed_snapshot_wins_over_older_available_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._snapshot(
                root, "112406", "2026-09-09T11:24:06+08:00",
                "2026-09-09T11:24:00+08:00", 1.342,
            )
            afternoon = self._snapshot(
                root, "132424", "2026-09-09T13:24:24+08:00",
                "2026-09-09T13:24:21+08:00", 1.330,
            )
            (root / "config" / "market").mkdir(parents=True, exist_ok=True)
            (root / "config" / "market" / "etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "515220", "name": "煤炭ETF"}]}),
                encoding="utf-8",
            )
            (root / "events" / "decisions").mkdir(parents=True)
            request = {
                "request_id": "decision-1325-explicit",
                "requested_at_beijing": "2026-09-09T13:25:00+08:00",
                "persistence_available_at_beijing": "2026-09-09T13:25:00+08:00",
                "market_date": "2026-09-09",
                "consumed_snapshot": afternoon,
                "formal_decision": {
                    "decision_id": "decision-1325-explicit",
                    "data_as_of_beijing": "2026-09-09T13:24:21+08:00",
                    "candidate_code": "515220",
                    "candidate_name": "煤炭ETF",
                    "main_candidate": "煤炭ETF（515220）",
                    "opportunity_status": "Trial机会",
                    "price_at_decision": 1.330,
                    "price_as_of_beijing": "2026-09-09T13:24:21+08:00",
                    "lifecycle": "",
                    "observation_management": [{"action": "RETAIN", "code": "515220", "name": "煤炭ETF", "thscode": "515220.SH", "thesis": "PIT测试中的既有观察身份保持不变", "falsifier": "本测试不评估交易假设", "next_decision_information": "仅验证PIT绑定", "information_value_reason": "保持既有观察管理合同完整"}],
                    "capital_competition": {
                        "next_unit_capital_use": "保留现金",
                        "full_competition_completed": True,
                        "releasable_capital_reviewed": True,
                        "post_action_deployable_cash": 10000.0,
                        "future_opportunity_capacity": "仍可承载后续Trial/Confirm",
                        "concentration_account_structure_effect": "不增加集中度",
                        "selected_state_reason": "现金优于当前可执行候选",
                        "new_amount_yuan": 0,
                        "zero_amount_decisive_reason": "完整资本竞争后保留现金",
                        "compared_capital_states": [
                            {
                                "state_name": "维持现有组合+现金",
                                "capital_action": "不新增",
                                "remaining_deployable_cash": 10000.0,
                                "why_not_selected": "已选中"
                            },
                            {
                                "state_name": "候选Trial+剩余现金",
                                "capital_action": "新增5000元",
                                "remaining_deployable_cash": 5000.0,
                                "why_not_selected": "未被当前决策选中"
                            }
                        ],
                        "held_etf_add_capital_reviews": [],
                        "capital_release_migrations": [],
                        "etf_opportunity_reviews": [{"security_code": "515220", "category": "OBSERVED_ETF", "opportunity_status": "Trial机会", "conclusion": "继续观察", "reason": "本测试仅验证PIT绑定"}]
                    },
                },
            }
            with patch.object(sync, "ROOT", root):
                recorded, _ = sync.record_formal_decision(request)
            event = json.loads(
                (root / "events" / "decisions" / "decision-1325-explicit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(recorded)
            self.assertEqual(event["price_source_snapshot"], afternoon)
            self.assertEqual(event["price_source"], "FORMAL_DECISION_SUPPLIED_POINT_IN_TIME")
            self.assertEqual(event["price_at_decision"], 1.330)
            self.assertEqual(event["price_as_of_beijing"], "2026-09-09T13:24:21+08:00")
            self.assertEqual(event["comparison_snapshot"]["as_of_beijing"], "2026-09-09T13:24:24+08:00")


if __name__ == "__main__":
    unittest.main()

