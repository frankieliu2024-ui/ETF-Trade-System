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
        self.assertIn("唯一人类可读规范", index)
        self.assertIn("唯一规范性规则来源", impl_readme)
        self.assertNotIn("唯一人类可读运行规范为", impl_readme)
        self.assertIn("不得反向覆盖规范", impl_readme)


if __name__ == "__main__":
    unittest.main()
