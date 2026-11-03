import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class MaintenanceHealthFreshnessTests(unittest.TestCase):
    def test_account_update_invalidates_old_reconciliation_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "data" / "state"
            request_dir = root / "requests" / "live_snapshot"
            state.mkdir(parents=True)
            request_dir.mkdir(parents=True)

            account = {
                "status": "VALID",
                "updated_at": "2026-11-03T15:37:00+08:00",
                "total_asset": 171031.36,
                "stock_market_value": 55736.89,
                "cash": 115294.47,
                "positions": [{"code": "159056", "quantity": 4700}],
            }
            request = request_dir / "broker.json"
            request.write_text(json.dumps({"account_fact": account}), encoding="utf-8")
            (state / "account_fact.json").write_text(json.dumps(account), encoding="utf-8")
            (state / "maintenance_health.json").write_text(
                json.dumps({
                    "status": "PASS",
                    "account_fact_updated_at": "2026-10-27T15:00:00+08:00",
                    "reconciliation": {"status": "PASS"},
                }),
                encoding="utf-8",
            )

            command = [
                sys.executable,
                "scripts/record_account_sync_maintenance.py",
                "--root", str(root),
                "--request-id", "broker-request",
                "--request-path", "requests/live_snapshot/broker.json",
                "--owner-outcome", "success",
                "--run-id", "test-run",
            ]
            result = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

            health = json.loads((state / "maintenance_health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["status"], "MAINTENANCE_INCOMPLETE")
            self.assertEqual(health["failure_class"], "PROJECTION_INCOMPLETE")
            self.assertTrue(health["reconciliation_stale"])
            self.assertIn("reconciliation", health["projection_required"])

    def test_rebuilt_projection_can_restore_pass_only_after_same_fact_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "data" / "state"
            request_dir = root / "requests" / "live_snapshot"
            state.mkdir(parents=True)
            request_dir.mkdir(parents=True)
            account = {
                "status": "VALID",
                "updated_at": "2026-11-03T15:37:00+08:00",
                "total_asset": 171031.36,
                "stock_market_value": 55736.89,
                "cash": 115294.47,
                "positions": [{"code": "159056", "quantity": 4700}],
            }
            (request_dir / "broker.json").write_text(
                json.dumps({"account_fact": account}), encoding="utf-8"
            )
            (state / "account_fact.json").write_text(json.dumps(account), encoding="utf-8")
            (state / "maintenance_health.json").write_text(
                json.dumps({"status": "PASS", "account_fact_updated_at": "2026-10-27T15:00:00+08:00"}),
                encoding="utf-8",
            )
            (root / "data" / "state" / "etf_strategy_equity.json").write_text(
                json.dumps({
                    "replay_cutoff": "2026-11-03",
                    "summary": {"coverage_status": "COMPLETE"},
                }),
                encoding="utf-8",
            )
            (root / "ETF当前状态_DASHBOARD.md").write_text(
                "账户事实更新时间：2026-11-03T15:37:00+08:00\n", encoding="utf-8"
            )
            command = [
                sys.executable,
                "scripts/record_account_sync_maintenance.py",
                "--root", str(root),
                "--request-id", "broker-request",
                "--request-path", "requests/live_snapshot/broker.json",
                "--owner-outcome", "success",
                "--run-id", "test-run",
            ]
            result = subprocess.run(
                command, cwd=Path(__file__).parents[1], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            health = json.loads(
                (state / "maintenance_health.json").read_text(encoding="utf-8")
            )
            self.assertEqual(health["status"], "PASS")
            self.assertEqual(health["projection_status"], "REBUILT_AND_READ_BACK")
            self.assertFalse(health["reconciliation_stale"])


if __name__ == "__main__":
    unittest.main()
