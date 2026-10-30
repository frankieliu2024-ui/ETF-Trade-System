import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sync_formal_files import normalize_dashboard_projection, latest_canonical_formal_decision, format_position_pnl, build_dashboard_block, render_decision_projection
from process_state_sync_request import latest_formal_review_decision


class DashboardProjectionTests(unittest.TestCase):
    def test_normalize_hides_historical_corrections_but_keeps_registered_anchors_and_derives_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calendar = root / "config/market"
            calendar.mkdir(parents=True)
            (calendar / "a_share_trading_calendar_2026.json").write_text(
                json.dumps({"closed_dates": ["2026-09-03"]}), encoding="utf-8"
            )
            text = (
                "top\n### 下一关键节点\nold\n"
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->\n"
                "FEE_old｜历史费用\n"
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->\n"
            )
            result = normalize_dashboard_projection(
                text, root, {"updated_at": "2026-09-02T21:05:00+08:00"}
            )
            self.assertIn(
                "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->\n<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->",
                result,
            )
            self.assertNotIn("FEE_old", result)
            self.assertIn("下一可交易节点：A股下一交易日 2026-09-04", result)
            self.assertIn("不生成订单", result)

    def test_latest_formal_review_wins_over_narrow_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "events/reviews"
            review_dir.mkdir(parents=True)
            (review_dir / "2026-09-02.json").write_text(
                json.dumps({
                    "updated_at_beijing": "2026-09-02T23:18:34+08:00",
                    "review": {
                        "reviewed_at_beijing": "2026-09-02T22:00:00+08:00",
                        "risk_permission": "允许Trial",
                        "main_candidate": "电网设备ETF（159326）",
                        "action": "盘后不新增交易。",
                        "lifecycle": {"159326": "Trial继续成立", "515880": "Trial已关闭"},
                        "data_time": {"a_share_effective_close_beijing": "2026-09-02T15:00:00+08:00"},
                    },
                }, ensure_ascii=False), encoding="utf-8"
            )
            result = latest_formal_review_decision(root)
            self.assertEqual(result["risk_permission"], "允许Trial")
            self.assertEqual(result["main_candidate"], "电网设备ETF（159326）")
            self.assertIn("515880", result["lifecycle"])

    def test_completed_report_presentation_populates_dashboard_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "events" / "reviews").mkdir(parents=True)
            (root / "data" / "state").mkdir(parents=True)
            (root / "events" / "reviews" / "formal_test.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION_REPORT",
                "reviewed_at_beijing": "2026-10-27T14:55:57+08:00",
                "presentation_binding": {
                    "full_content": "风险许可为保留现金、管理既有仓位、暂不新增。候选中仅将银行ETF富国（159887）评为机会ETF。建议新增部署0元，实际释放0份。"
                }
            }, ensure_ascii=False), encoding="utf-8")
            (root / "data" / "state" / "account_fact.json").write_text(json.dumps({
                "updated_at": "2026-10-27T15:36:44+08:00",
                "positions": []
            }), encoding="utf-8")
            decision = latest_canonical_formal_decision(root)
            self.assertEqual(decision["risk_permission"], "保留现金、管理既有仓位、暂不新增")
            self.assertIn("银行ETF富国", decision["main_candidate"])
            self.assertIn("新增部署0元", decision["amount_action"])

    def test_latest_dashboard_decision_ignores_stage_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "events/decisions"
            path.mkdir(parents=True)
            (path / "formal.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_time_beijing": "2026-09-14T11:20:00+08:00",
                "formal_decision": {
                    "risk_permission": "禁止新增",
                    "main_candidate": "现金｜无机会",
                    "data_as_of_beijing": "2026-09-14T11:20:00+08:00",
                },
            }, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(latest_canonical_formal_decision(root)["risk_permission"], "禁止新增")

    def test_latest_formal_decision_projects_canonical_capital_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "events/decisions"
            path.mkdir(parents=True)
            (path / "formal.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_time_beijing": "2026-09-28T22:51:36+08:00",
                "formal_decision": {
                    "risk_permission": "禁止新增",
                    "main_candidate": "800红利低波ETF（159355）",
                    "next_unit_capital_use": "下一单位资本最有效用途：现金21,962.84元；新增金额0元。",
                    "capital_competition": {
                        "new_amount_yuan": 0,
                        "zero_amount_decisive_reason": "广泛风险收缩且无对象通过完整新增许可与Trial/Confirm链。",
                    },
                    "data_as_of_beijing": "2026-09-28T22:51:36+08:00",
                },
            }, ensure_ascii=False), encoding="utf-8")
            result = latest_canonical_formal_decision(root)
            self.assertIn("现金21,962.84元", result["amount_action"])
            self.assertIn("新增金额0.00元", result["amount_action"])
            self.assertEqual(result["decisive_reason"], "广泛风险收缩且无对象通过完整新增许可与Trial/Confirm链。")

    def test_latest_formal_decision_keeps_legacy_display_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "events/decisions"
            path.mkdir(parents=True)
            (path / "formal.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_time_beijing": "2026-09-14T11:20:00+08:00",
                "formal_decision": {
                    "risk_permission": "禁止新增",
                    "amount_action": "旧格式动作",
                    "decisive_reason": "旧格式原因",
                },
            }, ensure_ascii=False), encoding="utf-8")
            result = latest_canonical_formal_decision(root)
            self.assertEqual(result["amount_action"], "旧格式动作")
            self.assertEqual(result["decisive_reason"], "旧格式原因")

    def test_position_pnl_is_exact_estimated_or_unknown(self):
        self.assertIn("-10.00元", format_position_pnl({"pnl": -10, "pnl_pct": -2}))
        self.assertIn("估算", format_position_pnl({"market_value": 90, "cost": 1, "quantity": 100}))
        self.assertEqual(format_position_pnl({"market_value": 90}), "—")

    def test_dashboard_cost_price_fallback_is_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "159999", "name": "测试ETF"}]}), encoding="utf-8"
            )
            rendered = build_dashboard_block(
                {"positions": [{"name": "测试ETF", "code": "159999", "quantity": 100, "cost_price": 1.234, "current_price": 1.250, "market_value": 125.0}],
                 "total_asset": 125.0, "stock_market_value": 125.0},
                {"summary": {}},
                "",
                root,
            )
            self.assertIn("|测试ETF（159999）|100|1.234|1.250|125.00元|", rendered)

    def test_dashboard_builder_renders_selector_dict_as_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config/market").mkdir(parents=True)
            (root / "config/market/etf_monitor_universe.json").write_text(
                json.dumps({"objects": [{"code": "510300", "name": "沪深300ETF"}]}), encoding="utf-8"
            )
            decision_dir = root / "events/decisions"
            decision_dir.mkdir(parents=True)
            (decision_dir / "formal.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_time_beijing": "2026-09-15T10:00:00+08:00",
                "formal_decision": {"risk_permission": "禁止新增", "main_candidate": "现金"},
            }, ensure_ascii=False), encoding="utf-8")
            account = {"positions": [], "total_asset": 1, "stock_market_value": 0}
            result = build_dashboard_block(account, {"risk_permission": "禁止新增"}, "", root)
            self.assertIn("### 最近一次正式决策", result)
            self.assertNotIn("{'risk_permission'", result)

    def test_trade_correction_writer_has_no_dashboard_destination(self):
        source = (ROOT / "scripts/apply_trade_fact_correction.py").read_text(encoding="utf-8")
        self.assertNotIn("upsert_formal_line(ROOT, DASHBOARD", source)


    def test_legacy_literal_newlines_are_recovered_as_real_markdown(self):
        legacy = "# ETF当前状态_DASHBOARD\\n\\n## 云端实时状态（自动同步）\\n\\n|标的|数量|\\n|-|-|\\n|黄金ETF（518880）|500|\\n\\n## 当前状态使用边界\\n\\n### 下一关键节点\\n- 下一A股交易日：2026-09-04"
        result = normalize_dashboard_projection(legacy, Path("."), {"updated_at": "2026-09-03T11:21:20+08:00"})
        self.assertGreater(len(result.splitlines()), 1)
        self.assertNotIn("\\n", result)
        self.assertEqual(result.splitlines()[0], "# ETF当前状态_DASHBOARD")
        self.assertIn("## 云端实时状态（自动同步）", result.splitlines())
        self.assertIn("|标的|数量|", result.splitlines())
        self.assertIn("## 当前状态使用边界", result.splitlines())

    def test_dashboard_newline_normalization_is_idempotent(self):
        legacy = "# ETF当前状态_DASHBOARD\\n\\n## 云端实时状态（自动同步）\\n\\n### 当前持仓事实"
        once = normalize_dashboard_projection(legacy, Path("."), {"updated_at": "2026-09-03T11:21:20+08:00"})
        twice = normalize_dashboard_projection(once, Path("."), {"updated_at": "2026-09-03T11:21:20+08:00"})
        self.assertEqual(once, twice)
        self.assertNotIn("\\n", twice)

    def test_current_dashboard_is_real_multiline_markdown(self):
        dashboard = (ROOT / "ETF当前状态_DASHBOARD.md").read_text(encoding="utf-8")
        self.assertGreater(len(dashboard.splitlines()), 1)
        self.assertNotIn("\\\\n", dashboard)
        self.assertIn("# ETF当前状态_DASHBOARD", dashboard.splitlines())
        self.assertIn("## 云端实时状态（自动同步）", dashboard.splitlines())

    def test_old_decision_is_historical_when_account_positions_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision_dir = root / "events/decisions"
            state_dir = root / "data/state"
            decision_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            (decision_dir / "formal.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_time_beijing": "2026-10-13T07:59:54+08:00",
                "formal_decision": {
                    "risk_permission": "禁止新增",
                    "lifecycle": {
                        "宁德时代（300750）": "持有管理",
                        "纳指ETF（159941）": "持有管理",
                    },
                    "data_as_of_beijing": "2026-10-13T07:59:54+08:00",
                },
            }, ensure_ascii=False), encoding="utf-8")
            (state_dir / "account_fact.json").write_text(json.dumps({
                "updated_at": "2026-10-16T15:00:00+08:00",
                "positions": [{"code": "159941", "name": "纳指ETF"}],
            }, ensure_ascii=False), encoding="utf-8")
            result = latest_canonical_formal_decision(root)
            self.assertEqual(result["_current_applicability"], "HISTORICAL")
            rendered = render_decision_projection(result)
            self.assertIn("历史正式决策", rendered)
            self.assertIn("不作为当前权限或当前持仓状态", rendered)

    def test_newest_completed_formal_report_supersedes_old_decision_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision_dir = root / "events/decisions"
            review_dir = root / "events/reviews"
            state_dir = root / "data/state"
            decision_dir.mkdir(parents=True)
            review_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            (decision_dir / "old.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION",
                "decision_time_beijing": "2026-10-13T07:59:54+08:00",
                "formal_decision": {
                    "risk_permission": "禁止新增",
                    "main_candidate": "旧现金",
                    "lifecycle": {"宁德时代（300750）": "持有管理"},
                },
            }, ensure_ascii=False), encoding="utf-8")
            (review_dir / "formal_new.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION_REPORT",
                "reviewed_at_beijing": "2026-10-18T09:52:07+08:00",
                "formal_decision": {"answers": {
                    "RISK_PERMISSION": {"final_action": "禁止新增"},
                    "MAIN_CANDIDATE": {"candidate_name": "现金"},
                    "NEXT_UNIT_CAPITAL_USE": {
                        "final_action": "现金",
                        "new_amount_yuan": 0,
                        "selected_state_reason": "现金优先",
                    },
                    "HOLDING:159941": {"final_action": "HOLD"},
                    "HOLDING:159981": {"final_action": "HOLD"},
                    "HOLDING:159992": {"final_action": "EXIT"},
                }},
            }, ensure_ascii=False), encoding="utf-8")
            (state_dir / "account_fact.json").write_text(json.dumps({
                "updated_at": "2026-10-16T15:00:00+08:00",
                "positions": [
                    {"code": "159941"}, {"code": "159981"}, {"code": "159992"}
                ],
            }), encoding="utf-8")
            result = latest_canonical_formal_decision(root)
            self.assertTrue(result["_decision_path"].endswith("events/reviews/formal_new.json"))
            self.assertEqual(result["_current_applicability"], "CURRENT")
            self.assertEqual(result["main_candidate"], "现金")
            self.assertIn("159992：EXIT", result["lifecycle"])



    def test_direct_completion_report_projects_actual_decision_sections_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "events/reviews"
            state_dir = root / "data/state"
            review_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            body = """【当前正式判断】

现金优先、持仓管理优先，不新增部署。Trial不授予，Confirm不授予；建议新增部署0元，实际释放0份。

【全市场机会与资本竞争】

现金保留优于新增部署；当前没有合格替代机会胜出现金，也没有持仓追加胜出现金。

【全部实际持仓】

纳指ETF（159941）：继续持有观察，不追加、不主动减持。继续占资优势尚未充分证明。
能源化工（159981）：继续持有观察，暂不追加。暂无明确破位。
创新药（159992）：继续持有管理，不追加、不主动退出。成长弱势仍在。

【执行边界】

本次仅形成管理建议。"""
            (review_dir / "formal_direct.json").write_text(json.dumps({
                "event_type": "FORMAL_DECISION_REPORT",
                "reviewed_at_beijing": "2026-10-30T11:02:29+08:00",
                "presentation_binding": {"full_content": body},
            }, ensure_ascii=False), encoding="utf-8")
            (state_dir / "account_fact.json").write_text(json.dumps({
                "updated_at": "2026-10-30T15:17:00+08:00",
                "positions": [
                    {"code": "159941"}, {"code": "159981"}, {"code": "159992"}
                ],
            }), encoding="utf-8")
            decision = latest_canonical_formal_decision(root)
            self.assertIn("Trial不授予", decision["risk_permission"])
            self.assertIn("无新的主候选", decision["main_candidate"])
            self.assertIn("新增部署0元", decision["amount_action"])
            self.assertIn("现金保留优于新增部署", decision["decisive_reason"])
            self.assertIn("纳指ETF（159941）：继续持有观察", decision["lifecycle"])
            self.assertEqual(decision["_current_applicability"], "HISTORICAL")
            self.assertIn("尚未纳入", decision["_stale_reason"])

if __name__ == "__main__":
    unittest.main()
