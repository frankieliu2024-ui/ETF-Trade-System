import json
import sys
import tempfile
from datetime import datetime
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_e2e_status
import check_system_consistency
import process_state_sync_request as sync


class PostCloseReviewCanonicalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "events/reviews").mkdir(parents=True)
        (self.root / "data/state").mkdir(parents=True)
        (self.root / "ETF市场行情档案_2026.md").write_text("## 6. 历史Excel与专项数据来源\n<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->\n<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->\n", encoding="utf-8")
        (self.root / "ETF交易复盘与经验库_2026.md").write_text("## 3. 历史研究与专项回测\n<!-- AUTO_CASE_DETAILS_START -->\n<!-- AUTO_CASE_DETAILS_END -->\n<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->\n<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->\n", encoding="utf-8")
        (self.root / "data/market/snapshots").mkdir(parents=True)
        close_snapshot = "data/market/snapshots/2026-08-31_150110.json"
        snapshot_rows = [{"quality_status": "PASS", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1}]
        (self.root / close_snapshot).write_text(json.dumps({
            "market_date": "2026-08-31", "node": "1500", "planned_time": "15:00",
            "quality_status": "PASS", "rows": snapshot_rows,
        }), encoding="utf-8")
        (self.root / "data/state/CURRENT.json").write_text(json.dumps({
            "market_date": "2026-08-31", "latest_valid_node": "1500", "latest_snapshot": close_snapshot,
        }) + "\n", encoding="utf-8")
        self.old = (sync.ROOT, sync.ARCHIVE, sync.EXPERIENCE, sync.ACCOUNT)
        sync.ROOT = self.root; sync.ARCHIVE = self.root / "ETF市场行情档案_2026.md"; sync.EXPERIENCE = self.root / "ETF交易复盘与经验库_2026.md"; sync.ACCOUNT = self.root / "data/state/account_fact.json"

    def tearDown(self):
        sync.ROOT, sync.ARCHIVE, sync.EXPERIENCE, sync.ACCOUNT = self.old
        self.tmp.cleanup()

    def request(self, when="2026-08-31T15:20:00+08:00"):
        return {"request_id": "review-20260831", "interaction_scenario": "POST_CLOSE_REVIEW", "requested_at_beijing": when, "formal_review": {"market_date": "2026-08-31", "reviewed_at_beijing": when, "case_id": "CASE-20260827-01", "case_mode": "CONTINUATION_NO_NEW_CASE", "data_time": {"close_snapshot": "data/market/snapshots/2026-08-31_150110.json"}, "archive_entry": "2026-08-31｜正式收盘事实归档。", "experience_entry": "2026-08-31｜延续既有Trial假设，不新增CASE。"}}

    def test_idempotent_and_closure(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}
        self.assertEqual(sync.record_post_close_review(account, self.request()), (True, False))
        self.assertEqual(sync.record_post_close_review(account, self.request()), (True, True))
        self.assertEqual(json.loads((self.root / "data/state/close_review_closure_2026-08-31.json").read_text(encoding="utf-8"))["status"], "CLOSED")

    def test_idempotent_partial_new_case_replay_restores_projections_without_event_rewrite(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}
        request = self.request()
        request["formal_review"].update({
            "case_id": "CASE-20260831-01",
            "case_mode": "NEW_CASE_FROM_EXECUTED_TRIAL",
            "experience_entry": "CASE-20260831-01：已执行Trial复盘，保留正式事实与边界。",
        })
        self.assertEqual(sync.record_post_close_review(account, request), (True, False))
        event_path = self.root / "events/reviews/2026-08-31.json"
        event_bytes = event_path.read_bytes()
        case_text = (self.root / "ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
        self.assertEqual(case_text.count("CASE-20260831-01"), 1)
        closure_path = self.root / "data/state/close_review_closure_2026-08-31.json"
        closure_path.unlink()
        (self.root / "ETF交易复盘与经验库_2026.md").write_text(
            case_text.replace("### 2.1 CASE-20260831-01：已执行Trial复盘，保留正式事实与边界。\n", ""),
            encoding="utf-8",
        )
        self.assertEqual(sync.record_post_close_review(account, request), (True, True))
        self.assertEqual(event_path.read_bytes(), event_bytes)
        restored = (self.root / "ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
        self.assertEqual(restored.count("CASE-20260831-01"), 1)
        self.assertEqual(json.loads(closure_path.read_text(encoding="utf-8"))["status"], "CLOSED")

    def test_stale_canonical_review_projects_existing_case_updates_idempotently_without_rewriting_pit(self):
        case_ids = ["CASE-20260902-01", "CASE-20260903-01", "CASE-20260909-01"]
        codes = ["159326", "518880", "515220"]
        event_ids = ["exit_159326", "exit_518880", "exit_515220"]
        details = [
            "### 2.16 CASE-20260902-01：电网设备ETF Trial执行复盘\n"
            "- 背景／生命周期：2026-09-02事前形成Trial；历史判断保持原样。\n"
            "- 关键证据：决策日证据只用于当时判断。\n- 执行：BUY事实见原始记录。\n"
            "- 结果：当时继续Trial。\n- 判断质量：按当时证据评估。\n- 执行质量：原始成交事实。\n"
            "- 风险收益质量：等待验证。\n- 资本使用效率：有限试错。\n"
            "- 最终结果／反事实：当时未能判断。\n- 经验：保留历史边界。",
            "### 2.17 CASE-20260903-01：黄金ETF Trial执行复盘\n"
            "- 背景／生命周期：2026-09-03建立Trial。\n- 关键证据：事前证据。\n"
            "- 执行：BUY事实。\n- 结果：继续持有。\n- 判断质量：待验证。\n"
            "- 执行质量：按原成交。\n- 风险收益质量：待验证。\n"
            "- 资本使用效率：有限试错。\n- 最终结果／反事实：当时开放。\n- 经验：不倒灌。",
            "### 2.18 CASE-20260909-01：煤炭ETF Trial｜2026-09-09真实买入；2026-09-09事前判断；不倒灌后续结果。",
        ]
        self.root.joinpath("ETF交易复盘与经验库_2026.md").write_text(
            "## 3. 历史研究与专项回测\n<!-- AUTO_CASE_DETAILS_START -->\n"
            + "\n".join(details)
            + "\n<!-- AUTO_CASE_DETAILS_END -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->\n<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->\n",
            encoding="utf-8",
        )
        updates = []
        trade_dir = self.root / "events/trades"
        trade_dir.mkdir(parents=True)
        for case_id, code, event_id in zip(case_ids, codes, event_ids):
            (trade_dir / f"{event_id}.json").write_text(json.dumps({
                "event_id": event_id, "code": code, "name": "ETF", "side": "SELL",
                "quantity": 100, "price": 1.23, "amount": 123.0,
                "confirmed_at_beijing": "2026-08-31T14:30:00+08:00",
                "execution_status": "EXECUTED",
            }), encoding="utf-8")
            updates.append({
                "trade_event_id": event_id, "case_id": case_id, "security_code": code,
                "case_status": "RESOLVED", "mapping_reason": f"{case_id}已全部退出并解决。",
            })
        # The canonical review may contain the same case mapping more than once;
        # projection must still emit one later-known update per CASE/date/field.
        updates.append(dict(updates[1]))

        request = self.request("2026-08-31T15:20:00+08:00")
        review = request["formal_review"]
        review["case_mapping"] = {"existing_case_updates": updates}
        review["experience_entry"] = "2026-08-31正式复盘确认三个CASE均完成退出。"
        prior = {
            "event_type": "FORMAL_POST_CLOSE_REVIEW", "market_date": "2026-08-31",
            "fingerprint": "canonical-existing-fingerprint", "reviewed_at_beijing": "2026-08-31T15:30:00+08:00",
            "updated_at_beijing": "2026-08-31T15:31:00+08:00", "review": json.loads(json.dumps(review)),
        }
        event_path = self.root / "events/reviews/2026-08-31.json"
        event_path.write_text(json.dumps(prior, ensure_ascii=False), encoding="utf-8")
        event_before = event_path.read_bytes()
        case_path = self.root / "ETF交易复盘与经验库_2026.md"
        text_before = case_path.read_text(encoding="utf-8")

        self.assertEqual(sync.record_post_close_review({"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}, request), (True, True))
        first = case_path.read_text(encoding="utf-8")
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertFalse((self.root / "data/state/close_review_closure_2026-08-31.json").exists())
        self.assertIn("2026-09-02事前形成Trial；历史判断保持原样。", first)
        self.assertIn("2026-09-09事前判断；不倒灌后续结果。", first)
        for case_id, event_id in zip(case_ids, event_ids):
            self.assertIn(f"### 2.{case_ids.index(case_id) + 16} {case_id}：", first)
            self.assertIn("- 背景／生命周期：", first)
            self.assertIn("- 关键证据：", first)
            self.assertIn("- 执行：", first)
            self.assertIn("- 最终结果／反事实：", first)
            self.assertIn("后续正式复盘（2026-08-31）", first)
            self.assertIn(f"trade_event_id={event_id}", first)
        self.assertIn("2026-09-09事前判断；不倒灌后续结果。", first)

        self.assertEqual(sync.record_post_close_review({"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}, request), (True, True))
        second = case_path.read_text(encoding="utf-8")
        self.maxDiff = None
        self.assertEqual(second, first)
        self.assertEqual(event_path.read_bytes(), event_before)
        self.assertEqual(second.count("后续正式复盘（2026-08-31）"), 9)
        section = second.split("### 2.17 CASE-20260903-01：", 1)[1].split("### 2.18", 1)[0]
        self.assertEqual(section.count("后续正式复盘（2026-08-31）"), 3)

    def test_new_case_uses_complete_shared_human_template(self):
        case_entry = sync._case_detail_projection_entry(
            "### CASE-20260831-01：短模板试验\n20260831_trade｜- 已归入CASE-20260831-01｜routing",
            "CASE-20260831-01",
        )
        for field in sync._CASE_TEMPLATE_FIELDS:
            self.assertIn(f"- {field}：", case_entry)
        self.assertNotIn("已归入CASE", case_entry)
        self.assertIn("信息不足（现有正式复盘未记录该字段）", case_entry)

    def test_case_mapping_normalizes_duplicate_trade_event_marker_idempotently(self):
        event_id = "BROKER_TRADE_20260909_133021_515220_BUY_3700"
        marker = f"<!-- TRADE_EVENT:{event_id} -->"
        mapped_row = "|2026-09-09 13:30:21|煤炭ETF|515220|BUY|3700|1.332|4928.40|EXECUTED|0|" + marker + " " + marker
        unrelated_row = "|2026-09-08 10:00:00|其他ETF|561980|BUY|100|1.000|100.00|EXECUTED|0|KEEP"
        self.root.joinpath("ETF交易复盘与经验库_2026.md").write_text(
            "## 3. 历史研究与专项回测\n"
            "<!-- AUTO_CASE_DETAILS_START -->\n<!-- AUTO_CASE_DETAILS_END -->\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->\n"
            + mapped_row + "\n" + unrelated_row + "\n"
            "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->\n",
            encoding="utf-8",
        )
        review = {"case_mapping": {"515220": {"trade_event_id": event_id, "case_id": "CASE-20260909-01"}}}
        sync.sync_experience_case_mapping_index(review)
        first = self.root.joinpath("ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
        self.assertEqual(first.count(marker), 1)
        self.assertIn("CASE-20260909-01", first)
        self.assertIn(unrelated_row, first)
        sync.sync_experience_case_mapping_index(review)
        second = self.root.joinpath("ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
        self.assertEqual(second, first)
        self.assertEqual(second.count(marker), 1)

    def test_stale_review_does_not_replace_newer(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T15:12:00+08:00"}
        sync.record_post_close_review(account, self.request("2026-08-31T15:30:00+08:00"))
        sync.record_post_close_review(account, self.request("2026-08-31T15:20:00+08:00"))
        event = json.loads((self.root / "events/reviews/2026-08-31.json").read_text(encoding="utf-8"))
        self.assertEqual(event["reviewed_at_beijing"], "2026-08-31T15:30:00+08:00")

    def test_midday_stage_does_not_consume_same_day_full_day_review_slot(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T12:00:00+08:00"}
        stage = self.request("2026-08-31T12:30:00+08:00")
        stage["market_phase"] = "MIDDAY_BREAK"
        stage["formal_review"]["review_scope"] = "MORNING_SESSION_STAGE_ONLY"
        stage["formal_review"]["market_phase"] = "MIDDAY_BREAK"
        stage["formal_review"]["data_time"]["close_snapshot"] = "data/market/snapshots/2026-08-31_113000.json"
        self.assertEqual(sync.record_post_close_review(account, stage), (False, False))
        self.assertFalse((self.root / "events/reviews/2026-08-31.json").exists())
        self.assertFalse((self.root / "data/state/close_review_closure_2026-08-31.json").exists())

        full_day = self.request("2026-08-31T20:30:00+08:00")
        full_day["formal_review"]["review_scope"] = "FULL_DAY"
        self.assertEqual(sync.record_post_close_review(account, full_day), (True, False))
        self.assertEqual(sync.record_post_close_review(account, full_day), (True, True))
        event = json.loads((self.root / "events/reviews/2026-08-31.json").read_text(encoding="utf-8"))
        closure = json.loads((self.root / "data/state/close_review_closure_2026-08-31.json").read_text(encoding="utf-8"))
        self.assertEqual(event["event_type"], "FORMAL_POST_CLOSE_REVIEW")
        self.assertEqual(event["review"]["data_time"]["close_snapshot"], "data/market/snapshots/2026-08-31_150110.json")
        self.assertEqual(closure["status"], "CLOSED")

    def test_live_snapshot_and_late_account_degrade_review_without_fake_close(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T16:18:00+08:00"}
        live_snapshot = "data/market/snapshots/2026-08-31_144800.json"
        (self.root / live_snapshot).write_text(json.dumps({
            "market_date": "2026-08-31", "node": "live", "planned_time": "14:48",
            "market_phase": "CONTINUOUS_AFTERNOON", "quality_status": "PASS",
            "rows": [{"quality_status": "PASS", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1}],
        }), encoding="utf-8")
        current = {"market_date": "2026-08-31", "latest_valid_node": "live", "latest_snapshot": live_snapshot}
        (self.root / "data/state/CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
        request = self.request("2026-08-31T20:30:00+08:00")
        request["formal_review"]["data_time"]["close_snapshot"] = live_snapshot
        self.assertEqual(sync.record_post_close_review(account, request), (True, False))
        event = json.loads((self.root / "events/reviews/2026-08-31.json").read_text(encoding="utf-8"))
        closure = json.loads((self.root / "data/state/close_review_closure_2026-08-31.json").read_text(encoding="utf-8"))
        self.assertEqual(event["event_type"], "FORMAL_POST_CLOSE_REVIEW")
        self.assertNotIn("close_snapshot", event["review"]["data_time"])
        self.assertEqual(event["review"]["data_time"]["close_data_status"], "UNVERIFIED")
        self.assertIn("VERIFIED_SESSION_CLOSE unavailable", event["review"]["data_time"]["close_data_gap"])
        self.assertEqual(closure["status"], "CLOSED")
        self.assertEqual(closure["close_snapshot"], "")

    def test_review_completes_when_no_close_snapshot_exists(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T16:18:00+08:00"}
        (self.root / "data/state/CURRENT.json").write_text(json.dumps({
            "market_date": "2026-08-31", "latest_valid_node": "live", "latest_snapshot": "",
        }), encoding="utf-8")
        request = self.request("2026-08-31T20:30:00+08:00")
        self.assertEqual(sync.record_post_close_review(account, request), (True, False))
        event = json.loads((self.root / "events/reviews/2026-08-31.json").read_text(encoding="utf-8"))
        self.assertEqual(event["event_type"], "FORMAL_POST_CLOSE_REVIEW")
        self.assertEqual(event["review"]["data_time"]["close_data_status"], "UNVERIFIED")
        self.assertNotIn("close_snapshot", event["review"]["data_time"])

    def test_mismatched_snapshot_is_rejected_when_verified_close_exists(self):
        account = {"status": "VALID", "updated_at": "2026-08-31T12:00:00+08:00"}
        bad_snapshot = "data/market/snapshots/2026-08-31_113000.json"
        (self.root / bad_snapshot).write_text(json.dumps({
            "market_date": "2026-08-31", "node": "live", "planned_time": "11:30",
            "quality_status": "PASS", "rows": [{"quality_status": "PASS", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1}],
        }), encoding="utf-8")
        current = {"market_date": "2026-08-31", "latest_valid_node": "1500", "latest_snapshot": "data/market/snapshots/2026-08-31_150110.json"}
        (self.root / "data/state/CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
        request = self.request("2026-08-31T20:30:00+08:00")
        request["formal_review"]["data_time"]["close_snapshot"] = bad_snapshot
        self.assertEqual(sync.record_post_close_review(account, request), (False, False))
        self.assertFalse((self.root / "events/reviews/2026-08-31.json").exists())
        self.assertFalse((self.root / "data/state/close_review_closure_2026-08-31.json").exists())

    def test_e2e_blocks_missing_canonical_review(self):
        event = self.root / "post_market_review/post_market_review_event.json"
        event.parent.mkdir()
        event.write_text(json.dumps({"market_close": True, "market_date": "2026-08-31", "status": "READY_FOR_REVIEW"}))
        (self.root / "config").mkdir()
        (self.root / "config/runtime_policy.json").write_text(
            json.dumps({"scheduled_trade_review": {"due_time": "20:30"}}),
            encoding="utf-8",
        )
        old = (
            build_e2e_status.POST_MARKET_REVIEW,
            build_e2e_status.REVIEW_DIR,
            build_e2e_status.STATE,
            build_e2e_status.ROOT,
        )
        build_e2e_status.POST_MARKET_REVIEW = event
        build_e2e_status.REVIEW_DIR = self.root / "events/reviews"
        build_e2e_status.STATE = self.root / "data/state"
        build_e2e_status.ROOT = self.root
        try:
            result = build_e2e_status.close_review_component(
                {"market_date": "2026-08-31"},
                datetime.fromisoformat("2026-08-31T20:31:00+08:00"),
            )
        finally:
            (
                build_e2e_status.POST_MARKET_REVIEW,
                build_e2e_status.REVIEW_DIR,
                build_e2e_status.STATE,
                build_e2e_status.ROOT,
            ) = old
        self.assertEqual(result["status"], "BLOCKED")


    def _write_runtime_policy(self):
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config/runtime_policy.json").write_text(
            json.dumps({"scheduled_trade_review": {"due_time": "20:30"}}),
            encoding="utf-8",
        )

    def _write_ready_trigger(self, market_date: str, market_close=True, status="READY_FOR_REVIEW"):
        event = self.root / "post_market_review/post_market_review_event.json"
        event.parent.mkdir(exist_ok=True)
        event.write_text(
            json.dumps({"market_close": market_close, "market_date": market_date, "status": status}),
            encoding="utf-8",
        )

    def _write_formal_review_completion(self, market_date: str, event_type="FORMAL_POST_CLOSE_REVIEW", closure_review_path=None, close_snapshot=""):
        review_dir = self.root / "events/reviews"
        review_dir.mkdir(parents=True, exist_ok=True)
        review_dir.joinpath(f"{market_date}.json").write_text(
            json.dumps({
                "event_type": event_type,
                "market_date": market_date,
                "review": {
                    "market_date": market_date,
                    "data_time": {"close_snapshot": close_snapshot},
                },
            }),
            encoding="utf-8",
        )
        closure_dir = self.root / "data/state"
        closure_dir.mkdir(parents=True, exist_ok=True)
        formal_review_path = closure_review_path or f"events/reviews/{market_date}.json"
        closure_dir.joinpath(f"close_review_closure_{market_date}.json").write_text(
            json.dumps({
                "status": "CLOSED",
                "market_date": market_date,
                "formal_review_path": formal_review_path,
            }),
            encoding="utf-8",
        )

    def _set_current_closure(self, market_date: str, status="CLOSED", path=None):
        current = {
            "market_date": market_date,
            "close_review_closure": {
                "status": status,
                "market_date": market_date,
                "formal_review_path": path or f"events/reviews/{market_date}.json",
            },
        }
        (self.root / "data/state/CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
        return current

    def _with_review_consumers(self, callback):
        e2e_old = (
            build_e2e_status.POST_MARKET_REVIEW,
            build_e2e_status.REVIEW_DIR,
            build_e2e_status.STATE,
            build_e2e_status.ROOT,
        )
        consistency_old = check_system_consistency.ROOT
        build_e2e_status.POST_MARKET_REVIEW = self.root / "post_market_review/post_market_review_event.json"
        build_e2e_status.REVIEW_DIR = self.root / "events/reviews"
        build_e2e_status.STATE = self.root / "data/state"
        build_e2e_status.ROOT = self.root
        check_system_consistency.ROOT = self.root
        try:
            return callback()
        finally:
            (
                build_e2e_status.POST_MARKET_REVIEW,
                build_e2e_status.REVIEW_DIR,
                build_e2e_status.STATE,
                build_e2e_status.ROOT,
            ) = e2e_old
            check_system_consistency.ROOT = consistency_old

    def _validate_consistency_review(self, now):
        report = {"checks": [], "errors": [], "warnings": []}
        check_system_consistency._validate_post_close_review_contract(report, now)
        return report

    def test_stale_readiness_trigger_with_newer_canonical_completion_converges_consumers(self):
        self._write_runtime_policy()
        self._write_ready_trigger("2026-09-13")
        self._write_formal_review_completion("2026-09-14", close_snapshot="")
        current = self._set_current_closure("2026-09-14")

        def run():
            e2e = build_e2e_status.close_review_component(
                current,
                datetime.fromisoformat("2026-09-14T20:31:00+08:00"),
            )
            consistency = self._validate_consistency_review(datetime.fromisoformat("2026-09-14T20:31:00+08:00"))
            return e2e, consistency

        e2e, consistency = self._with_review_consumers(run)

        self.assertEqual(e2e["status"], "READY")
        self.assertEqual(e2e["market_date"], "2026-09-14")
        self.assertEqual(e2e["formal_review_market_date"], "2026-09-14")
        self.assertEqual(e2e["market_trigger_market_date"], "2026-09-13")
        self.assertNotIn("market_close", e2e)
        self.assertNotIn("VERIFIED_SESSION_CLOSE", json.dumps(e2e))
        self.assertNotIn("15:00", json.dumps(e2e))
        self.assertEqual(consistency["checks"][-1]["status"], "PASS")
        self.assertIn("formal_review_market_date=2026-09-14", consistency["checks"][-1]["detail"])
        persisted_event = json.loads((self.root / "post_market_review/post_market_review_event.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted_event["market_date"], "2026-09-13")

    def test_same_date_closed_chain_keeps_existing_consumer_semantics(self):
        self._write_runtime_policy()
        self._write_ready_trigger("2026-09-14")
        self._write_formal_review_completion("2026-09-14")
        current = self._set_current_closure("2026-09-14")

        def run():
            return (
                build_e2e_status.close_review_component(current, datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
                self._validate_consistency_review(datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
            )

        e2e, consistency = self._with_review_consumers(run)

        self.assertEqual(e2e["status"], "READY")
        self.assertEqual(e2e["market_date"], "2026-09-14")
        self.assertEqual(consistency["checks"][-1]["status"], "PASS")

    def test_not_due_missing_completion_keeps_existing_semantics(self):
        self._write_runtime_policy()
        self._write_ready_trigger("2026-09-14")
        self._set_current_closure("2026-09-14", status="MISSING")

        def run():
            return (
                build_e2e_status.close_review_component({"market_date": "2026-09-14"}, datetime.fromisoformat("2026-09-14T19:00:00+08:00")),
                self._validate_consistency_review(datetime.fromisoformat("2026-09-14T19:00:00+08:00")),
            )

        e2e, consistency = self._with_review_consumers(run)

        self.assertEqual(e2e["status"], "READY")
        self.assertEqual(e2e["reason"], "scheduled_review_not_due")
        self.assertEqual(consistency["checks"][-1]["status"], "PASS")
        self.assertIn("review_not_due", consistency["checks"][-1]["detail"])

    def test_overdue_missing_completion_still_blocks_and_fails(self):
        self._write_runtime_policy()
        self._write_ready_trigger("2026-09-14")
        self._set_current_closure("2026-09-14", status="MISSING")

        def run():
            return (
                build_e2e_status.close_review_component({"market_date": "2026-09-14"}, datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
                self._validate_consistency_review(datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
            )

        e2e, consistency = self._with_review_consumers(run)

        self.assertEqual(e2e["status"], "BLOCKED")
        self.assertEqual(e2e["reason"], "FORMAL_POST_CLOSE_REVIEW_NOT_CANONICALIZED")
        self.assertEqual(consistency["checks"][-1]["status"], "FAIL")
        self.assertTrue(any(str(error).startswith("post_close_review:") for error in consistency["errors"]))

    def test_malformed_current_completion_fails_safe_to_event_due_logic(self):
        self._write_runtime_policy()
        self._write_ready_trigger("2026-09-14")
        self._write_formal_review_completion("2026-09-14", closure_review_path="events/reviews/2026-09-13.json")
        current = self._set_current_closure("2026-09-14")

        def run():
            return (
                build_e2e_status.close_review_component(current, datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
                self._validate_consistency_review(datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
            )

        e2e, consistency = self._with_review_consumers(run)

        self.assertEqual(e2e["status"], "BLOCKED")
        self.assertEqual(consistency["checks"][-1]["status"], "FAIL")
        self.assertIn("missing_or_invalid_closure", consistency["checks"][-1]["detail"])

    def test_non_formal_review_reference_fails_safe_to_event_due_logic(self):
        self._write_runtime_policy()
        self._write_ready_trigger("2026-09-14")
        self._write_formal_review_completion("2026-09-14", event_type="POST_MARKET_REVIEW_REQUIRED")
        current = self._set_current_closure("2026-09-14")

        def run():
            return (
                build_e2e_status.close_review_component(current, datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
                self._validate_consistency_review(datetime.fromisoformat("2026-09-14T20:31:00+08:00")),
            )

        e2e, consistency = self._with_review_consumers(run)

        self.assertEqual(e2e["status"], "BLOCKED")
        self.assertEqual(consistency["checks"][-1]["status"], "FAIL")
        self.assertIn("missing_or_invalid_review", consistency["checks"][-1]["detail"])


if __name__ == "__main__":
    unittest.main()

