from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.build_market_structure_context import _c2_evidence


class ParticipationStructureEvidenceTest(unittest.TestCase):
    def _write_panel(self, root: Path, *, final_amount: float = 150.0, final_close: float = 121.0) -> str:
        out = root / "events/research/daily_features"
        out.mkdir(parents=True, exist_ok=True)
        start = date(2026, 7, 1)
        trading = []
        d = start
        while len(trading) < 21:
            if d.weekday() < 5:
                trading.append(d)
            d += timedelta(days=1)
        for i, day in enumerate(trading):
            close = 100.0 + i * 0.5
            amount = 100.0
            if i == 20:
                close = final_close
                amount = final_amount
            payload = {
                "market_date": day.isoformat(),
                "quality_status": "PASS",
                "features": [{"code": "TEST01", "close": close, "amount": amount}],
            }
            (out / f"{day.isoformat()}.json").write_text(json.dumps(payload), encoding="utf-8")
        return trading[-1].isoformat()

    def test_ready_and_active_only_when_participation_and_structure_coexist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            market_date = self._write_panel(root)
            evidence = _c2_evidence(root, {"code": "TEST01"}, market_date)
            self.assertEqual(evidence["status"], "READY")
            self.assertTrue(evidence["breakout_20d"])
            self.assertTrue(evidence["research_group_participation_expanded"])
            self.assertTrue(evidence["enhancement_active"])
            self.assertEqual(evidence["direction"], "ENHANCE")
            self.assertFalse(evidence["can_generate_decision_independently"])
            self.assertIsNone(evidence["trade_signal"])

    def test_positive_structure_without_expanding_participation_is_not_active(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            market_date = self._write_panel(root, final_amount=105.0)
            evidence = _c2_evidence(root, {"code": "TEST01"}, market_date)
            self.assertEqual(evidence["status"], "READY")
            self.assertTrue(evidence["positive_structure"])
            self.assertFalse(evidence["research_group_participation_expanded"])
            self.assertFalse(evidence["enhancement_active"])
            self.assertEqual(evidence["direction"], "NOT_ACTIVE")

    def test_missing_latest_complete_bar_degrades_without_stale_direction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            market_date = self._write_panel(root)
            future_date = (date.fromisoformat(market_date) + timedelta(days=1)).isoformat()
            evidence = _c2_evidence(root, {"code": "TEST01"}, future_date)
            self.assertEqual(evidence["status"], "DEGRADED")
            self.assertIsNone(evidence["enhancement_active"])
            self.assertFalse(evidence["use_as_decision_evidence"])


if __name__ == "__main__":
    unittest.main()
