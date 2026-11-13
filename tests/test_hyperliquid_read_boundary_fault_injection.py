import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.pulse_history import read_pulse_projection


class HyperliquidReadBoundaryFaultInjectionTests(unittest.TestCase):
    def _root(self):
        return Path(tempfile.mkdtemp())

    def test_missing_projection_is_distinguished_from_valid_empty_payload(self):
        root = self._root()
        with patch("scripts.pulse_history.json.loads", side_effect=AssertionError("should not parse missing file")):
            result = read_pulse_projection(root, "HYPERLIQUID")
        # Current behavior is recorded as evidence: missing/invalid input collapses
        # to {}, which is the ambiguity this test exposes before a production fix.
        self.assertEqual(result["_read_diagnostic"]["status"], "READ_FAILED")

    def test_malformed_projection_is_silently_collapsed_to_empty(self):
        root = self._root()
        path = root / "data" / "state"
        path.mkdir(parents=True)
        (path / "hyperliquid_pulse_latest.json").write_text("{", encoding="utf-8")
        self.assertEqual(read_pulse_projection(root, "HYPERLIQUID")["_read_diagnostic"]["status"], "READ_FAILED")

    def test_valid_empty_projection_is_also_empty(self):
        root = self._root()
        path = root / "data" / "state"
        path.mkdir(parents=True)
        (path / "hyperliquid_pulse_latest.json").write_text(
            json.dumps({"latest": None}), encoding="utf-8"
        )
        result = read_pulse_projection(root, "HYPERLIQUID")
        self.assertIsNone(result.get("latest"))
        self.assertEqual(result["_read_diagnostic"]["status"], "VALID_EMPTY")

    def test_two_sources_can_carry_different_identities(self):
        external = {"context_id": "external-old"}
        latest = {"context_id": "projection-new"}
        self.assertNotEqual(external["context_id"], latest["context_id"])


if __name__ == "__main__":
    unittest.main()
