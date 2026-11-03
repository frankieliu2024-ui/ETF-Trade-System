from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ActiveReturnEventDetailsTests(unittest.TestCase):
    def test_stage1_writes_pit_event_details_without_decision_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config/research").mkdir(parents=True)
            (root / "events/research/daily_features").mkdir(parents=True)
            config = {
                "research_id": "test_active_return",
                "data": {
                    "source": "events/research/daily_features/*.json",
                    "start_date": "2026-01-01",
                    "end_date": "2026-04-10",
                    "point_in_time": "close D; execution starts at next trading-day open",
                },
                "horizons": [3, 5, 10, 20],
                "folds": [{"name": "all", "start": "2026-01-01", "end": "2026-04-10"}],
                "cost_scenarios_bps_round_trip": [10, 20, 30],
            }
            (root / "config/research/active_return_stage1.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            from datetime import date, timedelta

            day = date(2026, 1, 1)
            for i in range(100):
                d = day + timedelta(days=i)
                features = []
                for j, code in enumerate(("100001", "100002", "100003", "100004", "100005")):
                    close = 1.0 + i * (j + 1) * 0.01
                    features.append({"code": code, "open": close, "close": close, "quality_status": "PASS"})
                (root / "events/research/daily_features" / f"{d.isoformat()}.json").write_text(
                    json.dumps({"market_date": d.isoformat(), "market_phase": "POST_CLOSE_GRACE", "quality_status": "PASS", "features": features}),
                    encoding="utf-8",
                )
            env = {**os.environ, "ETF_SYSTEM_ROOT": str(root), "PYTHONPATH": str(Path(__file__).parents[1])}
            completed = subprocess.run(
                [sys.executable, "scripts/run_active_return_stage1.py"],
                cwd=Path(__file__).parents[1], env=env, check=False,
                capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            artifact = json.loads((root / "research/backtests/active_return_stage1_events.json").read_text())
            self.assertGreater(artifact["event_count"], 0)
            event = artifact["events"][0]
            self.assertIn("signal_date", event)
            self.assertIn("leader_forward_returns", event)
            self.assertIn("laggard_forward_returns", event)
            self.assertIn("leader_trades", event)
            trade = next(iter(event["leader_trades"].values()))
            self.assertIn("entry_date", trade)
            self.assertIn("entry_price", trade)
            self.assertIn("exit_date", trade)
            self.assertIn("exit_price", trade)
            self.assertAlmostEqual(trade["return"], event["leader_forward_returns"][next(iter(event["leader_forward_returns"]))], places=6)
            self.assertIn("cost_scenarios_bps_round_trip", event)
            self.assertFalse(artifact["historical_decision_reconstruction"])
            self.assertEqual(event["pit_rule"], config["data"]["point_in_time"])
            first = json.loads((root / "research/backtests/active_return_stage1_events.json").read_text())
            completed = subprocess.run(
                [sys.executable, "scripts/run_active_return_stage1.py"],
                cwd=Path(__file__).parents[1], env=env, check=False,
                capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            second = json.loads((root / "research/backtests/active_return_stage1_events.json").read_text())
            first.pop("generated_at_beijing", None)
            second.pop("generated_at_beijing", None)
            self.assertEqual(first, second)
            for path in (root / "events/research/daily_features").glob("*.json"):
                payload = json.loads(path.read_text())
                payload["quality_status"] = "FAIL"
                path.write_text(json.dumps(payload), encoding="utf-8")
            rejected = subprocess.run(
                [sys.executable, "scripts/run_active_return_stage1.py"],
                cwd=Path(__file__).parents[1], env=env, check=False,
                capture_output=True, text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("insufficient daily history", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
