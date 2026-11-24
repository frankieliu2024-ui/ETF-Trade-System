from pathlib import Path
import unittest


class AccountFastGuardContractTests(unittest.TestCase):
    def test_account_workflow_uses_account_fast_guard_scope(self):
        workflow = Path(".github/workflows/account-fact-maintenance.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/maintenance_guard.py --scope account-fast", workflow)

    def test_account_fast_scope_does_not_gate_on_derived_context_or_global_health(self):
        source = Path("scripts/maintenance_guard.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--scope", choices=("full", "account-fast"), default="full")', source)
        self.assertIn('if account_fast_scope:', source)
        self.assertIn('overall_ok = rec["status"] == "PASS"', source)
        self.assertIn('"global_health_status": "NOT_EVALUATED" if account_fast_scope', source)


if __name__ == "__main__":
    unittest.main()
