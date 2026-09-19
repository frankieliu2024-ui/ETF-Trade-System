from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    "513800": "data/market/on_demand/datasets/issue687_v0_513800_20230101_20260918.csv",
    "159687": "data/market/on_demand/datasets/issue687_v0_159687_20230101_20260918.csv",
    "513650": "data/market/on_demand/datasets/issue687_v0_513650_20230101_20260918.csv",
    "513310": "data/market/on_demand/datasets/issue687_v0_513310_20230101_20260918.csv",
    "159577": "data/market/on_demand/datasets/issue687_v0_159577_20230101_20260918.csv",
    "513810": "data/market/on_demand/datasets/issue687_v0_513810_20230101_20260918.csv",
    "513290": "data/market/on_demand/datasets/issue687_v0_513290_20230101_20260918.csv",
    "513360": "data/market/on_demand/datasets/issue687_v0_513360_20230101_20260918.csv",
}


class Issue687V0PITReplayTests(unittest.TestCase):
    def test_eight_case_replay_is_bounded_and_emits_research_result(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "result.json"
            cmd = [
                sys.executable,
                str(ROOT / "research/issue-687/evaluate_v0_pit.py"),
                "--output",
                str(out),
            ]
            for code, path in DATASETS.items():
                cmd.extend(["--dataset", f"{code}={path}"])
            subprocess.run(cmd, cwd=ROOT, check=True)
            result = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(result["issue"], 687)
        self.assertEqual(len(result["etfs"]), 8)
        self.assertIn("selection_bias_warning", result)
        for item in result["etfs"]:
            self.assertGreater(item["eligible_days"], 0)
            self.assertGreaterEqual(item["event_count"], 0)
            self.assertIn("never discovery inputs", item["research_boundary"])


if __name__ == "__main__":
    unittest.main()
