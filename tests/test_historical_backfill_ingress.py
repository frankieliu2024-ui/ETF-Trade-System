import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import process_state_sync_request as sync
from scripts import run_historical_backfill as runner


class HistoricalBackfillIngressTests(unittest.TestCase):
    def _request(self, root):
        return {
            "ingress_mode": "HISTORICAL_BACKFILL",
            "historical_fact_adopted_at": "2026-09-16T13:00:00+08:00",
            "source_period": "2026-07-13..2026-09-15",
            "historical_trades": [{
                "event_id": "historical_20260901_159326_buy",
                "code": "159326", "side": "BUY", "quantity": 3000, "price": 1.651,
                "executed_at": "2026-09-02T14:17:53+08:00",
                "market_date": "2026-09-02", "account_updated_at": "2026-09-16T09:00:00+08:00",
            }],
            "acquisition": {
                "code": "301689",
                            },
        }

    def test_backfill_is_idempotent_and_does_not_change_current_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events/trades").mkdir(parents=True)
            state = root / "data/state"
            state.mkdir(parents=True)
            current = {"market_date": "2026-09-16", "market_phase": "CONTINUOUS_AFTERNOON", "cash": 1234}
            account = {"status": "VALID", "cash": 1234, "positions": [{"code": "301689", "quantity": 500}], "formal_action": {"lifecycle": "持有管理"}}
            (state / "CURRENT.json").write_text(json.dumps(current), encoding="utf-8")
            (state / "account_fact.json").write_text(json.dumps(account), encoding="utf-8")
            before_current, before_account = copy.deepcopy(current), copy.deepcopy(account)
            req = self._request(root)
            with patch.object(sync, "ROOT", root):
                first = sync.process_historical_backfill_request(req)
                second = sync.process_historical_backfill_request(req)
            self.assertEqual(first["created"], 2)
            self.assertEqual(second["created"], 0)
            self.assertEqual(json.loads((state / "CURRENT.json").read_text()), before_current)
            self.assertEqual(json.loads((state / "account_fact.json").read_text()), before_account)
            acquisition = json.loads((root / "events/trades/HISTORICAL_ACQUISITION_20260901_301689_500.json").read_text())
            self.assertEqual(acquisition["event_type"], "IPO_ALLOTMENT_ACQUISITION")
            trade = json.loads((root / "events/trades/historical_20260901_159326_buy.json").read_text())
            self.assertEqual(trade["executed_at"], "2026-09-02T14:17:53+08:00")
            self.assertEqual(trade["confirmation_time_semantics"], "CANONICAL_ADOPTION_TIME")
            self.assertNotEqual(trade["executed_at"], trade["confirmed_at_beijing"])

    def _runner_manifest(self, count=37, acquisition=True):
        return {
            "ingress_mode": "HISTORICAL_BACKFILL",
            "request_id": "req-623",
            "historical_trades": [
                {"event_id": f"ordinary-{i}", "code": "159326", "side": "BUY",
                 "quantity": 1, "price": 1.0, "executed_at": "2026-09-01T10:00:00+08:00"}
                for i in range(count)
            ],
            **({"acquisition": {"code": "301689"}} if acquisition else {}),
        }

    def test_runner_accepts_37_ordinary_plus_one_acquisition(self):
        trades, acquisition = runner.validate_manifest(self._runner_manifest())
        self.assertEqual(len(trades), 37)
        self.assertEqual(acquisition["code"], "301689")

    def test_runner_rejects_38_ordinary_plus_one_acquisition(self):
        with self.assertRaises(ValueError):
            runner.validate_manifest(self._runner_manifest(count=38))

    def test_runner_rejects_37_without_acquisition(self):
        with self.assertRaises(ValueError):
            runner.validate_manifest(self._runner_manifest(acquisition=False))

    def test_runner_rejects_acquisition_inside_ordinary_trades(self):
        manifest = self._runner_manifest()
        manifest["historical_trades"][0]["event_type"] = "IPO_ALLOTMENT_ACQUISITION"
        with self.assertRaises(ValueError):
            runner.validate_manifest(manifest)

    def test_main_routes_historical_manifest_to_existing_owner(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "events/trades").mkdir(parents=True)
            (root / "data/state").mkdir(parents=True)
            req_path = root / "request.json"
            request = self._runner_manifest(count=37)
            req_path.write_text(json.dumps(request), encoding="utf-8")
            output = io.StringIO()
            with patch.object(sync, "ROOT", root), patch("sys.argv", ["process_state_sync_request.py", "request.json"]):
                with patch("sys.stdout", output):
                    self.assertEqual(sync.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["canonical_ingress_state"], sync.CANONICAL_INGRESS_SUBMITTED)
            self.assertTrue(result["trade_event_recorded"])
            self.assertEqual(result["account_sync_status"], "NOT_APPLICABLE")

    def test_runner_exposes_child_failure_diagnostics(self):
        completed = Mock(returncode=7, stdout="partial output", stderr="Traceback: secret=hidden")
        with patch.object(runner.subprocess, "run", return_value=completed):
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit):
                    runner._run_ingress(["python", "process_state_sync_request.py", "request.json"])
        diagnostic = json.loads(err.getvalue())
        self.assertEqual(diagnostic["historical_ingress_failure"], "SUBPROCESS_FAILED")
        self.assertEqual(diagnostic["returncode"], 7)
        self.assertIn("Traceback", diagnostic["stderr"])
        self.assertNotIn("hidden", diagnostic["stderr"])

    def test_runner_exposes_no_json_diagnostics(self):
        completed = Mock(returncode=0, stdout="partial output", stderr="owner traceback")
        with patch.object(runner.subprocess, "run", return_value=completed):
            with patch("sys.stderr", new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit):
                    runner._run_ingress(["python", "process_state_sync_request.py", "request.json"])
        diagnostic = json.loads(err.getvalue())
        self.assertEqual(diagnostic["historical_ingress_failure"], "CANONICAL_JSON_MISSING")
        self.assertEqual(diagnostic["stderr"], "owner traceback")

    def test_runner_extracts_final_json_after_prefix_logs(self):
        payload = {"canonical_ingress_state": "CANONICAL_INGRESS_SUBMITTED"}
        output = "informational log\\nrequest diagnostics\\n" + json.dumps(payload) + "\\n"
        self.assertEqual(runner._extract_final_json(output), payload)

    def test_runner_accepts_pure_json_output(self):
        payload = {"canonical_ingress_state": "CANONICAL_INGRESS_SUBMITTED"}
        self.assertEqual(runner._extract_final_json(json.dumps(payload)), payload)

    def test_runner_rejects_malformed_or_missing_json(self):
        for output in ("log only\\n", "{not-json}\\n"):
            with self.assertRaises(SystemExit):
                runner._extract_final_json(output)

    def test_runner_rejects_not_applicable_even_with_zero_exit(self):
        completed = Mock(returncode=0, stdout=json.dumps({
            "canonical_ingress_state": "CANONICAL_INGRESS_NOT_APPLICABLE",
            "canonical_ingress_failure_reason": "not_a_formal_fact_ingress_request",
        }), stderr="")
        with patch.object(runner.subprocess, "run", return_value=completed):
            with self.assertRaises(SystemExit):
                runner._run_ingress(["python", "process_state_sync_request.py", "request.json"])

    def test_reliable_historical_confirmation_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "events/trades"
            d.mkdir(parents=True)
            existing = {"event_id": "e", "code": "159326", "side": "BUY", "quantity": 1, "price": 1.0,
                        "executed_at": "2026-09-01T10:00:00+08:00",
                        "confirmed_at_beijing": "2026-09-02T10:00:00+08:00",
                        "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION"}
            (d / "e.json").write_text(json.dumps(existing), encoding="utf-8")
            req = {"ingress_mode": "HISTORICAL_BACKFILL", "historical_fact_adopted_at": "2026-09-16T13:00:00+08:00",
                   "historical_trades": [{"event_id": "e", "code": "159326", "side": "BUY", "quantity": 1, "price": 1.0,
                   "executed_at": "2026-09-01T10:00:00+08:00"}]}
            with patch.object(sync, "ROOT", root):
                sync.process_historical_backfill_request(req)
            got = json.loads((d / "e.json").read_text())
            self.assertEqual(got["confirmed_at_beijing"], "2026-09-02T10:00:00+08:00")
            self.assertEqual(got["historical_fact_adopted_at"], "2026-09-16T13:00:00+08:00")

    def test_core_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "events/trades"
            d.mkdir(parents=True)
            existing = {"event_id": "e", "code": "159326", "side": "BUY", "quantity": 1, "price": 1.0, "executed_at": "2026-09-01T10:00:00+08:00"}
            (d / "e.json").write_text(json.dumps(existing), encoding="utf-8")
            req = {"ingress_mode": "HISTORICAL_BACKFILL", "historical_trades": [{
                "event_id": "e", "code": "159326", "side": "BUY", "quantity": 2, "price": 1.0,
                "executed_at": "2026-09-01T10:00:00+08:00", "confirmed_at_beijing": "2026-09-16T10:00:00+08:00",
            }]}
            with patch.object(sync, "ROOT", root):
                with self.assertRaises(ValueError):
                    sync.process_historical_backfill_request(req)


if __name__ == "__main__":
    unittest.main()
