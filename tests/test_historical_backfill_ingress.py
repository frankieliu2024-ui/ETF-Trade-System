import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import process_state_sync_request as sync


class HistoricalBackfillIngressTests(unittest.TestCase):
    def _request(self, root):
        return {
            "ingress_mode": "HISTORICAL_BACKFILL",
            "historical_trades": [{
                "event_id": "historical_20260901_159326_buy",
                "code": "159326", "side": "BUY", "quantity": 3000, "price": 1.651,
                "executed_at": "2026-09-02T14:17:53+08:00",
                "confirmed_at_beijing": "2026-09-16T10:00:00+08:00",
                "market_date": "2026-09-02", "account_updated_at": "2026-09-16T09:00:00+08:00",
            }],
            "acquisition": {
                "code": "301689",
                "confirmed_at_beijing": "2026-09-16T10:00:00+08:00",
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
