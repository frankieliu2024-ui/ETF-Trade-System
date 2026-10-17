import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LegacyProductionBoundaryTests(unittest.TestCase):
    """Issue #1447: legacy modules must be reachable only through canonical facades."""

    def test_production_workflows_call_canonical_facades(self):
        workflows = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (ROOT / ".github" / "workflows").glob("*.yml")
        )
        for name in (
            "build_account_stock_market_legacy.py",
            "query_time_market_refresh_legacy.py",
            "build_market_regime_context_legacy.py",
            "build_market_structure_context_legacy.py",
            "send_market_shock_notification_legacy.py",
        ):
            self.assertNotIn(f"python scripts/{name}", workflows)
            self.assertNotIn(f"scripts/{name}", workflows)

    def test_canonical_facades_are_legacy_import_boundary(self):
        pairs = {
            "scripts/build_account_stock_market.py": "build_account_stock_market_legacy",
            "scripts/query_time_market_refresh.py": "query_time_market_refresh_legacy",
            "scripts/build_market_regime_context.py": "build_market_regime_context_legacy",
            "scripts/build_market_structure_context.py": "build_market_structure_context_legacy",
            "scripts/send_market_shock_notification.py": "send_market_shock_notification_legacy",
        }
        for facade, legacy in pairs.items():
            text = (ROOT / facade).read_text(encoding="utf-8")
            self.assertIn(legacy, text)
            self.assertTrue(text.startswith("from __future__ import annotations"))

    def test_legacy_modules_do_not_define_new_workflow_entries(self):
        workflows = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (ROOT / ".github" / "workflows").glob("*.yml")
        )
        self.assertNotIn("query_time_market_refresh_legacy.py", workflows)
        self.assertNotIn("send_market_shock_notification_legacy.py", workflows)


if __name__ == "__main__":
    unittest.main()
