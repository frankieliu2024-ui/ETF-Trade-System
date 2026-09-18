import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_historical_backfill as runner
from scripts import process_state_sync_request as syncer


class Issue664HistoricalCaseProjectionTests(unittest.TestCase):
    def _event(self):
        return {
            "event_id": "historical_20260713093614_159941_BUY_6200",
            "executed_at": "2026-07-13T09:36:14+08:00",
            "code": "159941",
            "side": "BUY",
            "quantity": 6200,
            "price": 1.611,
        }

    def test_seed_restores_exact_existing_case_without_touching_trade_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "|2026-07-13 09:36:14|纳指ETF（159941）|159941|买入|6,200|1.611|9,988.20|5.00|-9,993.20|真实成交已执行| <!-- TRADE_EVENT:historical_20260713093614_159941_BUY_6200 -->\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            event = self._event()
            original = dict(event)
            with patch.object(runner, "ROOT", root):
                runner._seed_projection_case_annotation(event, "CASE-20260713-01")
            rendered = experience.read_text(encoding="utf-8")
            self.assertIn("|CASE-20260713-01；真实成交已执行|", rendered)
            self.assertEqual(event, original)

    def test_seed_is_idempotent_and_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experience = root / "ETF交易复盘与经验库_2026.md"
            experience.write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "|2026-07-13 09:36:14|纳指ETF（159941）|159941|买入|6,200|1.611|9,988.20|5.00|-9,993.20|CASE-20260713-01；真实成交已执行|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            with patch.object(runner, "ROOT", root):
                runner._seed_projection_case_annotation(self._event(), "CASE-20260713-01")
                with self.assertRaises(SystemExit):
                    runner._seed_projection_case_annotation(self._event(), "CASE-20260716-01")

    def test_seed_requires_exact_economic_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ETF交易复盘与经验库_2026.md").write_text(
                "### 2.1 2026-07-13以来完整证券成交索引\n"
                "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
                "|-|-|-|-:|-:|-:|-:|-:|-:|-|\n"
                "### 2.2 银证转账与非交易现金流水\n",
                encoding="utf-8",
            )
            with patch.object(runner, "ROOT", root):
                with self.assertRaises(SystemExit):
                    runner._seed_projection_case_annotation(self._event(), "CASE-20260713-01")

    def test_fact_enrichment_preserves_seeded_case_before_later_ineligibility(self):
        event = self._event() | {
            "historical_backfill": True,
            "replay_semantics": "FACT_ENRICHMENT_ONLY",
        }
        text = (
            "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|\n"
            "|2026-07-13 09:36:14|纳指ETF（159941）|159941|买入|6,200|1.611|9,988.20|5.00|-9,993.20|CASE-20260713-01；真实成交已执行| "
            "<!-- TRADE_EVENT:historical_20260713093614_159941_BUY_6200 -->\n"
        )
        with patch.object(syncer, "_review_case_ids_for_trade", return_value=[]):
            resolved = syncer._case_ids_for_transaction_projection(event, text, 0, len(text))
        self.assertEqual(resolved, ["CASE-20260713-01"])

    def test_existing_case_conflict_fails_closed(self):
        event = self._event()
        text = (
            "|2026-07-13 09:36:14|纳指ETF（159941）|159941|买入|6,200|1.611|9,988.20|5.00|-9,993.20|"
            "CASE-20260713-01；CASE-20260716-01；真实成交已执行| "
            "<!-- TRADE_EVENT:historical_20260713093614_159941_BUY_6200 -->\n"
        )
        with self.assertRaises(ValueError):
            syncer._case_ids_from_existing_experience_for_trade(event, text, 0, len(text))


if __name__ == "__main__":
    unittest.main()
