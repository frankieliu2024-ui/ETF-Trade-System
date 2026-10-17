from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# These are the only production facades currently allowed to bridge legacy
# implementations.  A facade is not proof that legacy logic is already
# recovery-only; this contract prevents a second production entry from
# bypassing the facade while migration remains a separate change.
FACADE_TO_LEGACY = {
    "scripts/build_account_stock_market.py": "build_account_stock_market_legacy",
    "scripts/query_time_market_refresh.py": "query_time_market_refresh_legacy",
    "scripts/build_market_regime_context.py": "build_market_regime_context_legacy",
    "scripts/build_market_structure_context.py": "build_market_structure_context_legacy",
    "scripts/send_market_shock_notification.py": "send_market_shock_notification_legacy",
}


class LegacyProductionBoundaryTests(unittest.TestCase):
    def test_all_known_legacy_bridges_have_a_single_canonical_facade(self):
        for facade, legacy_module in FACADE_TO_LEGACY.items():
            path = ROOT / facade
            self.assertTrue(path.exists(), facade)
            text = path.read_text(encoding="utf-8")
            imports = re.findall(
                rf"(?:import\s+{re.escape(legacy_module)}\b|"
                rf"from\s+(?:scripts\.)?{re.escape(legacy_module)}\s+import\b)",
                text,
            )
            self.assertGreaterEqual(len(imports), 1, facade)

    def test_production_workflows_do_not_directly_import_legacy_modules(self):
        legacy_modules = tuple(FACADE_TO_LEGACY.values())
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            for module in legacy_modules:
                if re.search(rf"(?:import\s+{re.escape(module)}\b|from\s+{re.escape(module)}\s+import\b)", text):
                    offenders.append(f"{path.relative_to(ROOT)}:{module}")
        self.assertEqual(offenders, [])

    def test_production_scripts_do_not_directly_import_legacy_modules(self):
        allowed_facades = {ROOT / facade for facade in FACADE_TO_LEGACY}
        offenders = []
        for path in sorted((ROOT / "scripts").glob("*.py")):
            if path in allowed_facades:
                continue
            text = path.read_text(encoding="utf-8")
            for module in FACADE_TO_LEGACY.values():
                if re.search(rf"(?:import\s+{re.escape(module)}\b|from\s+(?:scripts\.)?{re.escape(module)}\s+import\b)", text):
                    offenders.append(f"{path.relative_to(ROOT)}:{module}")
        self.assertEqual(offenders, [])

    def test_production_workflows_call_facades_not_legacy_files(self):
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            for legacy_module in FACADE_TO_LEGACY.values():
                legacy_file = legacy_module + ".py"
                if legacy_file in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{legacy_file}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
