import json
import tempfile
import unittest
from pathlib import Path

from scripts.pulse_history import append_pulse_history, read_pulse_projection


class BoundedPulseProjectionContractTest(unittest.TestCase):
    def test_append_writes_small_latest_projection_and_reader_prefers_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = append_pulse_history(
                root,
                kind="HYPERLIQUID",
                payload={
                    "market_date": "2026-11-08",
                    "generated_at_beijing": "2026-11-08T08:05:00+08:00",
                    "observations": [{"asset": {"name": "XBI"}, "quality_status": "READY"}],
                },
                path="data/state/overseas_context.json#hyperliquid",
                identity="hl-test-1",
            )
            latest_path = root / "data" / "state" / "hyperliquid_pulse_latest.json"
            history_path = root / "data" / "state" / "hyperliquid_pulse_index.json"
            self.assertEqual(result["status"], "UPDATED")
            self.assertTrue(latest_path.exists())
            self.assertTrue(history_path.exists())
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            self.assertNotIn("days", latest)
            self.assertEqual(read_pulse_projection(root, "HYPERLIQUID")["latest"]["identity"], "hl-test-1")

    def test_reader_falls_back_to_legacy_history_only_when_projection_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "data" / "state"
            state.mkdir(parents=True)
            (state / "discovery_pulse_index.json").write_text(
                json.dumps({"latest": {"identity": "legacy-1"}}), encoding="utf-8"
            )
            self.assertEqual(read_pulse_projection(root, "DISCOVERY")["latest"]["identity"], "legacy-1")


if __name__ == "__main__":
    unittest.main()
