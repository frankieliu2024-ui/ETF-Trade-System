import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.process_state_sync_request import normalize_broker_account_fact_ingress


class AccountFactConsumptionContractTests(unittest.TestCase):
    def test_screenshot_aliases_are_normalized_into_latest_canonical_fact(self):
        request = {
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "submitted_at_beijing": "2026-10-22T18:34:15+08:00",
        }
        supplied = {
            "total_assets": 100.0,
            "available_cash": 60.0,
            "securities_market_value": 40.0,
            "positions": [{"code": "159941", "quantity": 10}],
        }
        normalized, moved = normalize_broker_account_fact_ingress(request, supplied)
        self.assertEqual(moved, 0)
        self.assertEqual(normalized["total_asset"], 100.0)
        self.assertEqual(normalized["cash"], 60.0)
        self.assertEqual(normalized["stock_market_value"], 40.0)
        self.assertEqual(normalized["updated_at"], "2026-10-22T18:34:15+08:00")

    def test_formal_workflow_does_not_scan_or_bind_historical_screenshot_requests(self):
        workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
        self.assertNotIn("newest unconsumed screenshot", workflow)
        self.assertNotIn("ACCOUNT_SYNC_REQUEST_FILE", workflow)

    def test_maintenance_receipt_fixture_is_request_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "requests/live_snapshot/broker.json"
            state = root / "data/state"
            request.parent.mkdir(parents=True)
            state.mkdir(parents=True)
            fact = {
                "updated_at": "2026-10-22T18:34:15+08:00",
                "total_asset": 100.0,
                "cash": 60.0,
                "stock_market_value": 40.0,
                "positions": [{"code": "159941", "quantity": 10}],
            }
            request.write_text(json.dumps({"account_fact": fact}), encoding="utf-8")
            (state / "account_fact.json").write_text(json.dumps(fact), encoding="utf-8")
            self.assertTrue(request.exists())



    def test_floating_pnl_alias_updates_canonical_account_holding_pnl(self):
        request = {
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "submitted_at_beijing": "2026-10-30T15:17:00+08:00",
        }
        supplied = {
            "total_assets": 171134.27,
            "available_cash": 115294.47,
            "securities_market_value": 55839.80,
            "floating_pnl": 353.40,
            "positions": [{"code": "159941", "quantity": 12200, "asset_type": "ETF"}],
        }
        normalized, moved = normalize_broker_account_fact_ingress(request, supplied)
        self.assertEqual(moved, 0)
        self.assertEqual(normalized["holding_pnl"], 353.40)
        self.assertEqual(normalized["floating_pnl"], 353.40)

    def test_explicit_holding_pnl_takes_precedence_over_floating_pnl_alias(self):
        request = {
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "submitted_at_beijing": "2026-10-30T15:17:00+08:00",
        }
        supplied = {"holding_pnl": 320.0, "floating_pnl": 353.40}
        normalized, _ = normalize_broker_account_fact_ingress(request, supplied)
        self.assertEqual(normalized["holding_pnl"], 320.0)


    def test_receipt_accepts_canonical_convertible_bond_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "requests/live_snapshot/broker.json"
            state = root / "data/state"
            request.parent.mkdir(parents=True)
            state.mkdir(parents=True)
            supplied = {
                "updated_at": "2026-11-04T15:00:00+08:00",
                "total_asset": 171685.07,
                "stock_market_value": 56390.6,
                "cash": 115294.47,
                "positions": [{
                    "code": "127118",
                    "quantity": 10,
                    "asset_type": "CONVERTIBLE_BOND",
                    "security_type": "CONVERTIBLE_BOND",
                }],
            }
            canonical = {
                **supplied,
                "positions": [{
                    "code": "127118",
                    "quantity": 10,
                    "asset_type": "OTHER",
                    "security_type": "CONVERTIBLE_BOND",
                }],
            }
            request.write_text(json.dumps({"account_fact": supplied}), encoding="utf-8")
            (state / "account_fact.json").write_text(json.dumps(canonical), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_account_sync_maintenance.py",
                    "--root", str(root),
                    "--request-id", "broker",
                    "--request-path", "requests/live_snapshot/broker.json",
                    "--owner-outcome", "success",
                    "--run-id", "1",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            health = json.loads((state / "maintenance_health.json").read_text(encoding="utf-8"))
            self.assertEqual(health["account_sync"]["status"], "PASS")
            self.assertEqual(health["account_sync"]["readback_status"], "CONFIRMED")

if __name__ == "__main__":
    unittest.main()
