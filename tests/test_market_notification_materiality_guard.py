from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notification_materiality_guard as guard
import market_notification_common as notification_common


class NotificationMaterialityGuardTests(unittest.TestCase):
    def test_market_label_only_change_is_rejected(self):
        event = {
            "event_type": "MARKET_VALUE_ALERT",
            "security_code": "515880",
            "confirmation_context": {
                "event_category": "REVERSAL",
                "market_as_of_beijing": "2026-08-28T14:20:13+08:00",
            },
        }
        self.assertIn("excursion", guard.notification_evidence_error(event))

    def test_market_reversal_with_numeric_path_is_allowed(self):
        event = {
            "event_type": "MARKET_VALUE_ALERT",
            "security_code": "515880",
            "confirmation_context": {
                "event_category": "REVERSAL",
                "market_as_of_beijing": "2026-08-28T14:20:13+08:00",
                "event_magnitude_pct": 2.51,
                "day_change_pct": -0.88,
            },
        }
        self.assertEqual(guard.notification_evidence_error(event), "")

    def test_apac_coverage_or_tone_change_without_hstech_delta_is_rejected(self):
        event = {
            "event_type": "APAC_SESSION_SUMMARY",
            "title": "【收盘总结】亚太香港收盘后区域判断发生实质变化",
            "confirmation_context": {"session_node": "HK_LATE_UPDATE"},
        }
        self.assertIn("no comparable HSTECH delta", guard.notification_evidence_error(event))

    def test_apac_real_hstech_move_is_allowed(self):
        event = {
            "event_type": "APAC_SESSION_SUMMARY",
            "title": "【收盘总结】亚太香港收盘后区域判断发生实质变化",
            "confirmation_context": {
                "session_node": "HK_LATE_UPDATE",
                "hstech_change_since_primary_pct": -0.81,
            },
        }
        self.assertEqual(guard.notification_evidence_error(event), "")

    def test_a_share_role_text_is_local_not_overseas(self):
        from scripts.notification_semantics import shock_implication
        with patch("scripts.notification_semantics._account_context", return_value={"data": {}, "held_etfs": {}, "held_stocks": {}, "formal": {}}):
            implication, _action = shock_implication("000688", "科创50", "A_SHARE_INDEX", "A_SHARE")
        self.assertIn("A股本地风险偏好", implication)
        self.assertNotIn("海外/区域结构证据", implication)

    def test_overseas_role_text_keeps_external_transmission(self):
        from scripts.notification_semantics import shock_implication
        with patch("scripts.notification_semantics._account_context", return_value={"data": {}, "held_etfs": {}, "held_stocks": {}, "formal": {}}):
            implication, _action = shock_implication("HSTECH", "恒生科技指数", "INDEX", "ASIA")
        self.assertIn("海外/区域结构证据", implication)

    def test_etf_and_account_stock_roles_are_not_index_roles(self):
        from scripts.notification_semantics import shock_implication
        with patch("scripts.notification_semantics._account_context", return_value={"data": {}, "held_etfs": {"515880": {}}, "held_stocks": {"300750": {}}, "formal": {}}):
            etf_text, _ = shock_implication("515880", "通信ETF", "ETF", "A_SHARE")
            stock_text, _ = shock_implication("300750", "宁德时代", "ACCOUNT_STOCK", "A_SHARE")
        self.assertIn("当前持仓ETF", etf_text)
        self.assertIn("账户个股", stock_text)

    def test_us_proxy_role_keeps_external_transmission(self):
        from scripts.notification_semantics import shock_implication
        with patch("scripts.notification_semantics._account_context", return_value={"data": {}, "held_etfs": {}, "held_stocks": {}, "formal": {}}):
            text_value, _ = shock_implication("SOXX", "半导体ETF代理", "ETF", "US")
        self.assertIn("海外/区域结构证据", text_value)

    def test_fixed_session_summary_is_not_misclassified_as_change(self):
        event = {
            "event_type": "A_SHARE_SESSION_SUMMARY",
            "title": "【收盘总结】A股全天结构与ETF强弱",
            "confirmation_context": {"session_node": "CLOSE"},
        }
        self.assertEqual(guard.notification_evidence_error(event), "")

    def test_formal_decision_missing_previous_fact_is_rejected(self):
        event = {
            "event_type": "FORMAL_DECISION_MATERIAL_CHANGE",
            "related_decision_id": "d2",
            "security_code": "515880",
            "confirmation_context": {
                "decision_id": "d2",
                "opportunity_status": "Trial机会",
                "previous_opportunity_status": "",
                "risk_permission": "允许Trial",
                "previous_risk_permission": "",
            },
        }
        self.assertIn("before/after", guard.notification_evidence_error(event))

    def test_formal_decision_real_risk_change_is_allowed(self):
        event = {
            "event_type": "FORMAL_DECISION_MATERIAL_CHANGE",
            "related_decision_id": "d2",
            "security_code": "515880",
            "confirmation_context": {
                "decision_id": "d2",
                "opportunity_status": "观察机会",
                "previous_opportunity_status": "观察机会",
                "security_code": "515880",
                "previous_security_code": "515880",
                "risk_permission": "禁止新增",
                "previous_risk_permission": "允许Trial",
            },
        }
        self.assertEqual(guard.notification_evidence_error(event), "")

    def test_account_change_requires_reconciliable_event_ids_and_time(self):
        bad = {"event_type": "ACCOUNT_FACT_CONFIRMATION", "confirmation_context": {}}
        self.assertIn("event ids", guard.notification_evidence_error(bad))
        good = {
            "event_type": "ACCOUNT_FACT_CONFIRMATION",
            "confirmation_context": {
                "account_event_ids": ["acct-1"],
                "account_event_time_beijing": "2026-08-28T14:18:00+08:00",
            },
        }
        self.assertEqual(guard.notification_evidence_error(good), "")

    def test_execution_confirmation_requires_linked_decision_and_side(self):
        bad = {
            "event_type": "PENDING_EXECUTION_CONFIRMATION",
            "security_code": "515880",
            "confirmation_context": {"security_code": "515880", "side": "BUY", "lifecycle": "Trial"},
        }
        self.assertIn("linked decision", guard.notification_evidence_error(bad))
        good = {
            "event_type": "PENDING_EXECUTION_CONFIRMATION",
            "security_code": "515880",
            "related_decision_id": "d1",
            "confirmation_context": {
                "decision_id": "d1",
                "security_code": "515880",
                "side": "BUY",
                "lifecycle": "Trial",
            },
        }
        self.assertEqual(guard.notification_evidence_error(good), "")

    def test_generic_decision_trigger_requires_explicit_evidence_change(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            trigger = {
                "status": "TRIGGERED",
                "requires_formal_reassessment": True,
                "idempotency_key": "k1",
                "trigger_type": "RESEARCH_EVIDENCE_CHANGED",
                "evidence_change": "",
            }
            (state / "decision_trigger.json").write_text(json.dumps(trigger), encoding="utf-8")
            with patch.object(guard, "STATE", state):
                event = {"type": "交易判断", "source": "decision_trigger"}
                self.assertIn("no explicit evidence change", guard.notification_evidence_error(event))
                trigger["evidence_change"] = "relative strength evidence materially changed"
                (state / "decision_trigger.json").write_text(json.dumps(trigger), encoding="utf-8")
                self.assertEqual(guard.notification_evidence_error(event), "")

    def test_system_diagnostic_must_belong_to_current_main(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            diag = {
                "recommended_action": "ESCALATE_WITH_DIAGNOSTIC",
                "run_id": "123",
                "safety": {"head_is_current_main": False},
            }
            (state / "workflow_failure_diagnostic.json").write_text(json.dumps(diag), encoding="utf-8")
            with patch.object(guard, "STATE", state):
                event = {"type": "系统异常", "source": "workflow_failure_diagnostic"}
                self.assertIn("not for current main", guard.notification_evidence_error(event))
                diag["safety"]["head_is_current_main"] = True
                (state / "workflow_failure_diagnostic.json").write_text(json.dumps(diag), encoding="utf-8")
                self.assertEqual(guard.notification_evidence_error(event), "")

    def test_production_notification_workflows_use_guarded_entrypoint(self):
        decision = (ROOT / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        overseas = (ROOT / ".github/workflows/overseas-preopen-pulse.yml").read_text(encoding="utf-8")
        us = (ROOT / ".github/workflows/us-extended-hours-pulse.yml").read_text(encoding="utf-8")
        for text in (decision, overseas, us):
            self.assertIn("run_guarded_notification.py", text)
        self.assertNotIn("run: python scripts/send_regional_session_summary.py", decision)
        self.assertNotIn("run: python scripts/send_market_shock_notification.py", decision)
        self.assertNotIn("run: python scripts/notification_center.py --mode", decision)
        self.assertNotIn("run: python scripts/send_regional_session_summary.py", overseas)
        self.assertNotIn("run: python scripts/send_market_shock_notification.py", overseas)
        self.assertNotIn("run: python scripts/send_us_session_summary.py", overseas)
        self.assertNotIn("run: python scripts/send_market_shock_notification.py", us)
        self.assertNotIn("run: python scripts/send_us_session_summary.py", us)

    def test_notification_state_persistence_merges_after_main_refresh(self):
        decision = (ROOT / ".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("merge_notification_state.py", decision)
        self.assertNotIn("cp /tmp/etf-notification-state/data/state/notification_center.json data/state/notification_center.json", decision)

    def test_notification_rule_source_governance_is_single_and_explicit(self):
        spec = (ROOT / "docs/ETF主动通知体系.md").read_text(encoding="utf-8")
        index = (ROOT / "ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")
        impl_readme = (ROOT / "notifications/README.md").read_text(encoding="utf-8")

        self.assertIn("唯一规范性规则来源", spec)
        self.assertIn("运行事实以当前 `main` 的脚本、配置和状态为准；规则解释以本文为准", spec)
        self.assertIn("若实现与本文冲突，视为实现漂移", spec)
        self.assertIn("所有声称“发生变化 / 实质变化 / 升级 / 失效 / 恢复”的主动通知", spec)
        self.assertIn("scripts/notification_materiality_guard.py", spec)
        self.assertIn("scripts/run_guarded_notification.py", spec)
        self.assertIn("docs/ETF主动通知体系.md", index)
        self.assertIn("主动通知域", index)
        self.assertIn("唯一规范性规则来源", index)
        self.assertIn("唯一规范性规则来源", impl_readme)
        self.assertNotIn("唯一人类可读运行规范为", impl_readme)
        self.assertIn("不得反向覆盖规范", impl_readme)


    def test_intraday_summary_uses_current_not_close(self):
        import notification_semantics as semantics

        indices = {
            "000001": {"symbol": "000001", "provider_name": "上证指数", "change_pct": -1.0},
            "000688": {"symbol": "000688", "provider_name": "科创50", "change_pct": -2.0},
            "399006": {"symbol": "399006", "provider_name": "创业板指", "change_pct": -1.0},
        }
        etfs = [{"symbol": "515880", "provider_name": "通信ETF", "change_pct": -1.5}]
        formal = {"validity": "ACTIVE", "applicable_object": "515880", "lifecycle": "Trial"}
        with patch.object(semantics, "_account_context", return_value={"held_etfs": {}, "held_stocks": {}, "formal": formal}):
            headline, _, _, _ = semantics.a_share_structure(indices, etfs, {}, is_close=False)
        text = " ".join(headline)
        self.assertIn("当前", text)
        self.assertNotIn("收盘", text)

    def test_close_summary_retains_close_wording(self):
        import notification_semantics as semantics

        indices = {
            "000001": {"symbol": "000001", "provider_name": "上证指数", "change_pct": -1.0},
            "000688": {"symbol": "000688", "provider_name": "科创50", "change_pct": -2.0},
            "399006": {"symbol": "399006", "provider_name": "创业板指", "change_pct": -1.0},
        }
        etfs = [{"symbol": "515880", "provider_name": "通信ETF", "change_pct": -1.5}]
        formal = {"validity": "ACTIVE", "applicable_object": "515880", "lifecycle": "Trial"}
        with patch.object(semantics, "_account_context", return_value={"held_etfs": {}, "held_stocks": {}, "formal": formal}):
            headline, _, _, _ = semantics.a_share_structure(indices, etfs, {}, is_close=True)
        self.assertIn("收盘", " ".join(headline))


class NotificationAggregationTests(unittest.TestCase):
    def test_same_fact_key_is_stable_across_notification_runs(self):
        from scripts import send_market_shock_notification_legacy as legacy
        c = {"category": "REVERSAL", "code": "HSTECH", "day": 0.32, "event_magnitude_pct": 1.61, "sudden": 0.0, "direction": "UP", "name": "恒生科技指数", "latest": {"as_of_beijing": "2026-08-31T16:09:08+08:00"}}
        current_path = legacy.ROOT / "data/state/overseas_context.json"
        first = legacy._build_context_event(c, source="asia", current_path=current_path, current={}, labels={}, metric_labels={}, market_dates={"HSTECH": "2026-08-31"}, today_bj="2026-08-31")
        second = legacy._build_context_event(c, source="asia", current_path=current_path, current={}, labels={}, metric_labels={}, market_dates={"HSTECH": "2026-08-31"}, today_bj="2026-08-31")
        self.assertEqual(first["key"], second["key"])
        self.assertIn("16:09:08", first["key"])

    @staticmethod
    def _event(code, name, category, direction="UP", stamp="2026-08-31T13:20:00+08:00", magnitude=2.0, event_type="MARKET_VALUE_ALERT"):
        return {
            "event_type": event_type,
            "security_code": code,
            "security_name": name,
            "created_at": stamp,
            "sent_at": stamp,
            "title": f"【市场异动】{name}{category}",
            "content": f"{name}（{code}）当前涨跌 {magnitude:+.2f}%",
            "confirmation_context": {
                "market_date": "2026-08-31",
                "security_code": code,
                "security_name": name,
                "event_category": category,
                "direction": direction,
                "event_magnitude_pct": magnitude,
            },
        }

    def test_same_object_continuous_events_merge_and_keep_tags(self):
        first = self._event("N225", "日经225指数", "SUDDEN", magnitude=1.2, stamp="2026-08-31T10:00:00+08:00")
        second = self._event("N225", "日经225指数", "EXTREME", magnitude=1.4, stamp="2026-08-31T10:04:00+08:00")
        result = notification_common._absorb_or_aggregate([first], second)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "AGGREGATED_INTO_EXISTING")
        self.assertEqual(result[1]["confirmation_context"]["event_tags"], ["SUDDEN", "EXTREME"])

    def test_summary_absorbs_same_fact_but_not_independent_gold(self):
        summary = {
            "event_type": "A_SHARE_SESSION_SUMMARY",
            "created_at": "2026-08-31T15:02:00+08:00",
            "sent_at": "2026-08-31T15:02:00+08:00",
            "title": "【收盘总结】科创50日内V形修复",
            "content": "科创50（000688）从低点修复至高位。",
            "confirmation_context": {
                "market_date": "2026-08-31",
                "covered_fact_keys": ["2026-08-31|000688|REVERSAL|UP"],
            },
        }
        reversal = self._event("000688", "科创50", "REVERSAL", stamp="2026-08-31T15:03:00+08:00")
        gold = self._event("518880", "黄金ETF", "EXTREME", direction="DOWN", stamp="2026-08-31T15:03:00+08:00", magnitude=4.2)
        self.assertEqual(notification_common._absorb_or_aggregate([summary], reversal)[0], "ABSORBED_BY_SUMMARY")
        self.assertIsNone(notification_common._absorb_or_aggregate([summary], gold))

    def test_protected_trial_is_never_absorbed(self):
        summary = {
            "event_type": "A_SHARE_SESSION_SUMMARY",
            "created_at": "2026-08-31T15:02:00+08:00",
            "sent_at": "2026-08-31T15:02:00+08:00",
            "title": "【收盘总结】科创50结构修复",
            "content": "科创50（000688）修复。",
            "confirmation_context": {"market_date": "2026-08-31", "covered_fact_keys": ["2026-08-31|515880|REVERSAL|UP"]},
        }
        trial = self._event("515880", "通信ETF", "REVERSAL", stamp="2026-08-31T15:03:00+08:00", event_type="Trial机会")
        self.assertIsNone(notification_common._absorb_or_aggregate([summary], trial))

    def test_material_upgrade_reopens_after_summary_or_aggregation(self):
        summary = {
            "event_type": "A_SHARE_SESSION_SUMMARY",
            "created_at": "2026-08-31T15:02:00+08:00",
            "sent_at": "2026-08-31T15:02:00+08:00",
            "title": "【收盘总结】科创50日内V形修复",
            "content": "科创50（000688）修复。",
            "confirmation_context": {"market_date": "2026-08-31", "covered_fact_keys": ["2026-08-31|000688|REVERSAL|UP"], "covered_event_magnitude_pct": 2.0},
        }
        upgrade = self._event("000688", "科创50", "REVERSAL", stamp="2026-08-31T15:04:00+08:00", magnitude=5.0)
        self.assertIsNone(notification_common._absorb_or_aggregate([summary], upgrade))

    def test_apac_summary_title_names_objects_directly(self):
        source = Path(__file__).resolve().parents[1] / "scripts" / "send_regional_session_summary.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('subjects = "、".join(label for _, label, _, _ in selected[:3])', text)
        self.assertNotIn("title = f\"【收盘总结】亚太主要市场收盘", text)


if __name__ == "__main__":
    unittest.main()
