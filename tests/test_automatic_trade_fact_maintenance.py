import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "market-snapshot.yml"
CORRECTOR = ROOT / "scripts" / "apply_trade_fact_correction.py"


class AutomaticTradeFactMaintenanceContractTests(unittest.TestCase):
    def test_existing_market_snapshot_workflow_consumes_correction_ingress(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('requests/trade_fact_correction/**', text)
        self.assertIn("Apply confirmed trade fact corrections", text)
        self.assertIn("python scripts/apply_trade_fact_correction.py", text)
        self.assertIn("git push", text)

    def test_canonical_corrector_projects_all_required_fact_surfaces(self):
        text = CORRECTOR.read_text(encoding="utf-8")
        self.assertIn("events/trades", text)
        self.assertIn("ETF交易复盘与经验库_2026.md", text)
        self.assertIn("ETF市场行情档案_2026.md", text)
        self.assertIn("data/state/etf_strategy_equity.json", text)
        self.assertIn("upsert_formal_line", text)


if __name__ == "__main__":
    unittest.main()
