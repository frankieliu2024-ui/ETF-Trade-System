from __future__ import annotations

import os

from unittest.mock import patch

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import notification_center as center


class NotificationActiveReportContractTests(unittest.TestCase):
    def _request(self, report_type: str) -> dict:
        body = f"full report for {report_type}"
        return {
            "schema_version": "1.0",
            "channel": "REPORT",
            "report_type": report_type,
            "report_id": f"report-{report_type}",
            "task_id": report_type,
            "task_run_id": f"run-{report_type}",
            "generated_at": "2026-09-15T08:00:00+08:00",
            "effective_market_date": "2026-09-15",
            "source_actor": "ChatGPT",
            "source_reference": "product-task:test",
            "title": f"title-{report_type}",
            "summary": "summary",
            "full_content": body,
            "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "idempotency_key": f"idem-{report_type}",
            "delivery_mode": "FULL_REPORT",
            "no_trade_authority": True,
        }

    def test_canonical_report_builder_always_satisfies_validator_schema(self):
        request = center.build_report_delivery_request(
            task_id="ETF交易复盘", task_run_id="run-20260915-2030",
            report_id="report-20260915", report_type="ETF_TRADE_REVIEW",
            effective_market_date="2026-09-15",
            title="ETF交易复盘｜2026-09-15", summary="降级但正式完成",
            full_content="close evidence unavailable; no close inferred",
            source_reference="scheduled-trade-review:20260915",
            idempotency_key="ETF_TRADE_REVIEW:20260915:run-20260915-2030",
            generated_at="2026-09-15T20:30:00+08:00",
        )
        valid, reason = center.validate_report_delivery_request(request)
        self.assertTrue(valid, reason)
        for field in ("task_id", "task_run_id", "content_hash", "idempotency_key"):
            self.assertTrue(request[field])

    def test_canonical_report_builder_supports_system_review(self):
        request = center.build_report_delivery_request(
            task_id="ETF系统复核", task_run_id="run-20260920-0800",
            report_id="system-review-20260920-0800", report_type="ETF_SYSTEM_REVIEW",
            effective_market_date="2026-09-20",
            title="【系统复核】ETF系统复核｜2026-09-20 08:00", summary="系统复核完成",
            full_content="frozen system review body",
            source_reference="scheduled-system-review:20260920T0800",
            idempotency_key="ETF_SYSTEM_REVIEW:20260920:run-20260920-0800",
            generated_at="2026-09-20T08:00:00+08:00",
        )
        self.assertEqual(request["report_type"], "ETF_SYSTEM_REVIEW")
        valid, reason = center.validate_report_delivery_request(request)
        self.assertTrue(valid, reason)

    def test_canonical_report_builder_accepts_formal_decision_report_type(self):
        request = center.build_report_delivery_request(
            task_id="ETF正式决策", task_run_id="run-formal",
            report_id="formal", report_type="ETF_FORMAL_DECISION",
            effective_market_date="2026-09-20",
            title="【ETF正式决策】", summary="正式决策完成", full_content="frozen formal decision",
            source_reference="formal-decision", idempotency_key="ETF_FORMAL_DECISION:run-formal",
            generated_at="2026-09-20T08:00:00+08:00",
        )
        self.assertEqual(request["report_type"], "ETF_FORMAL_DECISION")
        self.assertTrue(center.validate_report_delivery_request(request)[0])

    def test_only_active_scheduled_report_types_are_accepted(self):
        for report_type in ("ETF_TRADE_REVIEW", "ETF_SYSTEM_REVIEW", "ETF_FORMAL_DECISION"):
            valid, reason = center.validate_report_delivery_request(self._request(report_type))
            self.assertTrue(valid, reason)

    def test_formal_decision_report_is_accepted_for_new_delivery(self):
        valid, reason = center.validate_report_delivery_request(self._request("ETF_FORMAL_DECISION"))
        self.assertTrue(valid, reason)
        self.assertIn("ETF_FORMAL_DECISION", center.ACTIVE_REPORT_TYPES)
        self.assertNotIn("ETF_FORMAL_DECISION", center.HISTORICAL_REPORT_TYPES)

    def test_historical_formal_report_notification_record_remains_readable(self):
        historical = {
            "notification_id": "historical-formal-report",
            "event_type": "REPORT_DELIVERY_REQUEST",
            "notification_channel": "REPORT",
            "delivery_mode": "FULL_REPORT",
            "source_event_id": "report-delivery:legacy-formal",
            "lifecycle_status": "SENT",
            "title": "legacy formal report",
            "content": "legacy content",
            "source": "legacy",
            "created_at": "2026-09-01T12:00:00+08:00",
            "sent_at": "2026-09-01T12:00:01+08:00",
            "confirmation_context": {},
        }
        normalized = center.normalize_notification(historical, historical)
        self.assertEqual(normalized["notification_channel"], "REPORT")
        self.assertEqual(normalized["lifecycle_status"], "SENT")
        self.assertEqual(normalized["title"], "legacy formal report")

    def test_manual_formal_decision_material_change_remains_interrupt_family(self):
        event = {
            "event_type": "FORMAL_DECISION_MATERIAL_CHANGE",
            "confirmation_context": {
                "opportunity_status": "Trial机会",
                "previous_opportunity_status": "观察机会",
                "risk_permission": "允许Trial",
                "previous_risk_permission": "允许Trial",
            },
        }
        self.assertEqual(center.canonical_template_family(event), "Trial机会")


class FormalFinalContentNotificationTests(unittest.TestCase):
    @unittest.skip("legacy events/decisions route removed; canonical review event is tested separately")
    def test_formal_notification_requires_exact_current_frozen_content(self):
        import scripts.notification_center as center
        from pathlib import Path
        import tempfile
        import json
        body = "唯一冻结正文"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            path.write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "parent_request_id": "parent-1",
                "decision_id": "decision-1",
                "presentation_binding": {
                    "request_id": "parent-1",
                    "parent_request_id": "parent-1",
                    "decision_id": "decision-1",
                    "report_id": "report-20261014T1120",
                    "full_content": body,
                    "content_hash": digest,
                    "title": "正式决策",
                },
            }), encoding="utf-8")
            with patch.dict(os.environ, {"DECISION_EVENT_PATH": str(path), "EXPECTED_FORMAL_REQUEST_ID": "parent-1"}, clear=False):
                event = center.formal_decision_report_event()
            self.assertIsNotNone(event)
            self.assertEqual(event["content"], body)
            self.assertEqual(event["content_hash"], digest)
            self.assertEqual(event["request_id"], "parent-1")
            self.assertEqual(event["parent_request_id"], "parent-1")
            self.assertEqual(event["report_id"], "report-20261014T1120")

    @unittest.skip("legacy events/decisions route removed; canonical review event is tested separately")
    def test_formal_notification_accepts_distinct_completion_and_parent_request_ids(self):
        import scripts.notification_center as center
        from pathlib import Path
        import tempfile
        import json
        body = "父子请求仍属同一正式判断"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            path.write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "presentation_binding": {
                    "request_id": "completion-1",
                    "parent_request_id": "parent-1",
                    "decision_id": "decision-1",
                    "report_id": "etf-formal-decision:decision-1",
                    "full_content": body,
                    "content_hash": digest,
                    "title": "正式决策",
                },
            }), encoding="utf-8")
            with patch.dict(os.environ, {
                "DECISION_EVENT_PATH": str(path),
                "EXPECTED_FORMAL_REQUEST_ID": "parent-1",
            }, clear=False):
                event = center.formal_decision_report_event()
            self.assertIsNotNone(event)
            self.assertEqual(event["request_id"], "completion-1")
            self.assertEqual(event["parent_request_id"], "parent-1")
            self.assertEqual(event["report_id"], "etf-formal-decision:decision-1")

    def test_formal_notification_rejects_missing_or_mismatched_binding(self):
        import scripts.notification_center as center
        from pathlib import Path
        import tempfile
        import json
        body = "唯一冻结正文"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.json"
            payload = {"event_type": "FORMAL_DECISION", "presentation_binding": {
                "request_id": "current", "parent_request_id": "current", "decision_id": "d",
                "report_id": "r", "full_content": body, "content_hash": "old-hash"
            }}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"DECISION_EVENT_PATH": str(path), "EXPECTED_FORMAL_REQUEST_ID": "current"}, clear=False):
                self.assertIsNone(center.formal_decision_report_event())


    def test_formal_decision_stale_notification_cannot_block_current_request(self):
        import scripts.notification_center as center

        current = {
            "report_type": "ETF_FORMAL_DECISION",
            "request_id": "fd-current",
            "parent_request_id": "fd-parent-current",
            "related_decision_id": "decision-current",
            "content_hash": "hash-current",
            "source_event_id": "formal-presentation:current",
        }
        stale = {
            "report_type": "ETF_FORMAL_DECISION",
            "request_id": "fd-old",
            "parent_request_id": "fd-parent-old",
            "related_decision_id": "decision-old",
            "content_hash": "hash-old",
            "source_event_id": "formal-presentation:current",
            "notification_id": "notification_old",
            "lifecycle_status": "SENT",
        }
        self.assertIsNone(center.find_existing_notification([stale], current))

    def test_formal_decision_same_current_identity_is_idempotent(self):
        import scripts.notification_center as center

        current = {
            "report_type": "ETF_FORMAL_DECISION",
            "request_id": "fd-current",
            "parent_request_id": "fd-parent-current",
            "task_run_id": "run-current",
            "occurrence_id": "occurrence-current",
            "related_decision_id": "decision-current",
            "content_hash": "hash-current",
        }
        existing = dict(current, notification_id="notification-current", lifecycle_status="SENT")
        self.assertEqual(center.find_existing_notification([existing], current), existing)




class FormalPresentationSemanticGateTests(unittest.TestCase):
    def _content(self, capital_text=None, holding_comparison=True):
        headings = [
            "【判断时点与事实边界】",
            "【当前正式判断】",
            "【风险与市场状态】",
            "【三层监测】",
            "【全市场发现逐只评估】",
            "【观察ETF逐只评估】",
            "【机会ETF逐只评估】",
            "【全市场机会与资本竞争】",
            "【现金管理】",
            "【全部实际持仓】",
            "【最终管理动作】",
            "【关键风险与改变判断的条件】",
            "【信息不足在哪里】",
            "【执行边界】",
            "【置信度】",
            "【最可能出错的地方】",
            "【后置交接状态】",
        ]
        sections = {
            "【判断时点与事实边界】": "本次判断使用本节点合法固定时段事实，区分行情实际时间、采集时间和账户事实时间。信息只截至本次运行开始前，不将后续事实倒灌。",
            "【当前正式判断】": "本次先完成风险许可，再比较机会、金额、现金、现有持仓和可释放资本。当前建议不自动构成交易执行，Trial与Confirm均需满足完整准入条件。",
            "【风险与市场状态】": "风险许可必须结合市场状态、账户风险和有效证据判断；本例风险许可尚允许继续评估机会，但不以风险提示代替金额判断。",
            "【三层监测】": "海外与跨市场、A股内部反馈、ETF与资本状态分别核验，数据缺失降低证据强度但不自动等于没有机会。",
            "【全市场发现逐只评估】": "1. 发现候选评估\n\n1）候选甲（159001）：仅为发现输入，尚未自动成为观察ETF或机会ETF。",
            "【观察ETF逐只评估】": "1. 观察对象评估\n\n1）观察甲（159002）：保留观察身份，仍需核验趋势、承接和独立假设。",
            "【机会ETF逐只评估】": "1. 机会对象评估\n\n1）当前候选尚未达到机会资格。Trial按5,000元标准评估，直接Confirm必须独立达到Confirm证据标准，三者与0元部署比较；当前Trial和Confirm均未成立，决定性缺口是持续性与承接证据不足，下一复核节点为下次正式决策。",
            "【全市场机会与资本竞争】": capital_text or "现金保留、持仓继续持有、追加、部分减持释放、全部退出以及合格机会之间必须进行全资本竞争。相较现金保留与其他机会，比较风险收益比、资本效率、机会成本和迁移成本。因为当前候选证据不足以胜出，当前胜出用途为保留现金并维持具备正面理由的持仓；未选择追加、减持释放和全部退出，是因为尚无证据证明其边际收益风险更优。下一单位资本当前最有效用途为保留现金。",
            "【现金管理】": "可部署现金和结算约束必须核验。实际新增部署为0元、实际释放为0份时，必须说明这是完整比较后的结论，而不是默认值。",
            "【全部实际持仓】": (
                "1. 持仓逐只比较\n\n"
                "1）纳指ETF（159941）：持有管理，不追加。相较现金、合格机会和替代用途，继续占资优于释放资本，因为当前风险收益仍可接受且迁移成本较高。若当前没有该持仓，按当前价格和证据仍愿意重新配置资本；继续持有、追加、部分减持和全部退出分别比较。继续持有保留旧仓赢家右尾，承担的失败风险可控，且保留账户功能；迁移成本需覆盖。"
                + ("相较继续持有与释放资本，当前没有足够证据证明释放后部署到其他机会更优；可释放资本暂不确认。若海外科技持续弱于市场或回撤扩大，再评估。" if holding_comparison else "当前继续持有，因为风险较高。")
                + "\n\n2）能源化工ETF（159981）：持有管理，不追加。相较现金、合格机会和替代用途，继续占资优于释放资本，因为趋势与相对强度仍占优；若当前没有该持仓，按当前价格和证据仍愿意重新配置资本；继续持有、追加、部分减持和全部退出分别比较。继续持有保留旧仓赢家右尾，承担的失败风险可控，且保留账户功能；迁移成本需覆盖。若趋势和相对强度恶化，再评估。"
                + "\n\n3）创新药ETF（159992）：持有管理，不追加。相较现金、合格机会和替代用途，继续占资优于释放资本，因为尚有反转修复的可证伪空间；若当前没有该持仓，按当前价格和证据仍愿意重新配置资本；继续持有、追加、部分减持和全部退出分别比较。继续持有保留旧仓赢家右尾，承担的失败风险可控，且保留账户功能；迁移成本需覆盖。若持续弱于市场或反转假设被证伪，再评估。"
                + "\n\n4）全指器械ETF（159056）：持有管理，不追加。相较现金、合格机会和替代用途，继续占资优于释放资本，因为行业配置功能仍有保留价值；若当前没有该持仓，按当前价格和证据仍愿意重新配置资本；继续持有、追加、部分减持和全部退出分别比较。继续持有保留旧仓赢家右尾，承担的失败风险可控，且保留账户功能；迁移成本需覆盖。若行业持续弱于市场，再评估。"
            ),
            "【最终管理动作】": "建议新增部署0元，实际新增部署0元，实际释放0份。继续管理持仓；Trial与Confirm按正式规则分别判断，不自动下单。",
            "【关键风险与改变判断的条件】": "下一节点验证风险许可、市场宽度、候选持续性、持仓相对强弱、现金竞争和账户新成交。",
            "【信息不足在哪里】": "部分资金流和盘口承接证据可能缺失；不得把信息不足伪装成已完成比较。",
            "【执行边界】": "所有内容仅为管理建议。用户是唯一交易负责人，系统不自动提交订单或改变交易权限。",
            "【置信度】": "整体置信度中等；结论受数据新鲜度、候选完整性和账户事实边界影响。",
            "【最可能出错的地方】": "可能低估候选机会或误判持仓相对优势；需用下一合法节点事实更新。",
            "【后置交接状态】": "正文形成、完成记录、事件、报告、通知投递和用户收到必须分别确认，不相互替代。",
        }
        return "【ETF正式决策】2026-10-29 11:04 正式判断\n\n" + "\n\n".join(
            heading + "\n\n" + sections[heading] for heading in headings
        )

    def test_unnumbered_named_holdings_are_checked_individually(self):
        import scripts.notification_center as center
        content = self._content()
        for code, name in (("159941", "纳指ETF"), ("159981", "能源化工ETF"),
                           ("159992", "创新药ETF"), ("159056", "全指器械ETF")):
            content = content.replace(name + "（" + code + "）", name + "（" + code + "）")
        import re
        content = re.sub(r"(?m)^\\d+）(?=[^\\n]*（\\d{6}）：)", "", content)
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content, "decision_response": {},
        })
        self.assertTrue(accepted, reason)

    def test_unnumbered_holding_without_release_comparison_is_rejected(self):
        import scripts.notification_center as center
        import re
        content = self._content()
        content = re.sub(r"(?m)^\\d+）(?=[^\\n]*（\\d{6}）：)", "", content)
        content = content.replace(
            "相较继续持有与释放资本，尚无完整反转证据；可释放资本暂不确认。",
            "目前暂时继续持有。",
        )
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content, "decision_response": {},
        })
        self.assertFalse(accepted)
        self.assertEqual(reason, "formal_presentation_position_missing_hold_vs_release_comparison")

    def test_live_prompt_section_labels_and_numbering_are_accepted(self):
        import scripts.notification_center as center

        content = self._content()
        for old, new in (
            ("【三层监测】", "【市场监测】"),
            ("【全市场发现逐只评估】", "【全市场发现】"),
            ("【观察ETF逐只评估】", "【观察ETF】"),
            ("【机会ETF逐只评估】", "【机会ETF】"),
        ):
            content = content.replace(old, new)
        import re
        content = re.sub(r"(?m)^(\\d+)）(?=\\s)", r"（\\1）", content)
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content,
            "decision_response": {},
        })
        self.assertTrue(accepted, reason)

    def test_live_prompt_format_does_not_bypass_capital_semantics(self):
        import scripts.notification_center as center

        content = self._content(capital_text="风险较高，所以新增部署0元。现金保留，持仓继续持有。")
        content = content.replace("【三层监测】", "【市场监测】")
        content = content.replace("【全市场发现逐只评估】", "【全市场发现】")
        content = content.replace("【观察ETF逐只评估】", "【观察ETF】")
        content = content.replace("【机会ETF逐只评估】", "【机会ETF】")
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content,
            "decision_response": {},
        })
        self.assertFalse(accepted)
        self.assertIn(reason, {
            "formal_presentation_missing_whole_capital_comparison",
            "formal_presentation_missing_capital_efficiency_comparison",
        })

    def test_missing_content_without_structured_answers_is_rejected(self):
        import scripts.notification_center as center

        accepted, reason = center.validate_formal_presentation_contract(
            {"decision_response": {}}
        )

        self.assertFalse(accepted)
        self.assertEqual(reason, "formal_presentation_missing_content")

    def test_weak_zero_deployment_rationale_is_rejected_without_structured_answers(self):
        import scripts.notification_center as center

        weak = self._content(
            capital_text="风险较高，所以新增部署0元。现金保留，持仓继续持有。"
        )
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": weak,
            "decision_response": {},
        })

        self.assertFalse(accepted)
        self.assertIn(reason, {
            "formal_presentation_missing_whole_capital_comparison",
            "formal_presentation_missing_capital_efficiency_comparison",
        })

    def test_complete_capital_and_position_comparisons_are_accepted(self):
        import scripts.notification_center as center

        content = self._content()
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content,
            "decision_response": {},
        })

        self.assertTrue(accepted, reason)
        self.assertEqual(reason, "")

    def test_keyword_complete_capital_without_alternatives_is_rejected(self):
        import scripts.notification_center as center
        content = self._content(capital_text=(
            "现金与持仓相较机会，资本效率和机会成本均已考虑。"
            "因为风险许可有限，现金保留、持仓继续持有，建议新增部署0元。"
        ))
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content, "decision_response": {},
        })
        self.assertFalse(accepted)
        self.assertEqual(reason, "formal_presentation_missing_capital_alternatives")

    def test_capital_alternatives_without_rationale_are_rejected(self):
        import scripts.notification_center as center
        content = self._content(capital_text=(
            "现金保留、持仓持有、追加、减持释放、退出、机会ETF均相较。"
            "资本效率与机会成本。"
        ))
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content, "decision_response": {},
        })
        self.assertFalse(accepted)
        self.assertEqual(reason, "formal_presentation_missing_capital_choice_rationale")

    def test_position_without_hold_vs_release_comparison_is_rejected(self):
        import scripts.notification_center as center

        content = self._content(holding_comparison=False)
        accepted, reason = center.validate_formal_presentation_contract({
            "presentation_content": content,
            "decision_response": {},
        })

        self.assertFalse(accepted)
        self.assertEqual(reason, "formal_presentation_position_missing_hold_vs_release_comparison")

    def _formal_content_event_for(self, content):
        import json
        import tempfile

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        event = {
            "request_type": "BUSINESS_DECISION_SOURCE",
            "request_id": "formal-request-current",
            "parent_request_id": "formal-request-current",
            "decision_id": "formal-decision-current",
            "presentation_content": content,
            "content_hash": digest,
            "presentation_binding": {
                "request_id": "formal-request-current",
                "parent_request_id": "formal-request-current",
                "decision_id": "formal-decision-current",
                "report_id": "formal-report-current",
                "full_content": content,
            },
            "decision_response": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current-bds.json"
            path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
            return center.formal_content_event(path)

    def test_delivery_entry_rejects_weak_zero_deployment_rationale(self):
        weak = self._content(
            capital_text="风险较高，所以新增部署0元。现金保留，持仓继续持有。"
        )

        self.assertIsNone(self._formal_content_event_for(weak))

    def test_delivery_entry_accepts_complete_semantic_presentation(self):
        event = self._formal_content_event_for(self._content())

        self.assertIsNotNone(event)
        self.assertEqual(event["content"], self._content())
        self.assertEqual(event["request_id"], "formal-request-current")

    def test_delivery_entry_rejects_holding_without_release_comparison(self):
        weak = self._content(holding_comparison=False)

        self.assertIsNone(self._formal_content_event_for(weak))


if __name__ == "__main__":
    unittest.main()
