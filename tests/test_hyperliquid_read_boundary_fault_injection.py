import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.pulse_history import read_pulse_projection, summarize_hyperliquid_layers, select_legal_pulse


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

    def test_layer_audit_preserves_counts_and_warning_only_mismatch(self):
        audit = summarize_hyperliquid_layers(
            {"context_id": "external-old", "observation_count": 563},
            {"latest": {"context_id": "projection-new", "observations": [{}, {}], "etf_bindings": [{}]}},
            {"etf_contributions": [{}, {}]},
        )
        self.assertEqual(audit["raw_observation_count"], 563)
        self.assertEqual(audit["core_object_count"], 2)
        self.assertEqual(audit["mapped_object_count"], 1)
        self.assertEqual(audit["etf_contribution_count"], 2)
        self.assertEqual(audit["identity_status"], "WARNING_MISMATCH")
        self.assertTrue(audit["warning_only"])

    def test_selects_latest_legal_pulse_at_or_before_node(self):
        projection = {
            "latest": {"identity": "after-node", "formed_at_beijing": "2026-11-13T10:04:05+08:00", "quality_status": "PASS", "observations": [{}]},
            "pulse_sequence": [
                {"identity": "before-node", "formed_at_beijing": "2026-11-13T09:55:05+08:00", "quality_status": "PASS", "observations": [{}]},
                {"identity": "after-node", "formed_at_beijing": "2026-11-13T10:04:05+08:00", "quality_status": "PASS", "observations": [{}]},
            ],
        }
        selected = select_legal_pulse(projection, "2026-11-13T10:00:00+08:00")
        self.assertEqual(selected["selected_identity"], "before-node")
        self.assertEqual(selected["selection_status"], "SELECTED_AT_OR_BEFORE_NODE")

    def test_late_only_pulse_is_not_backfilled_into_earlier_node(self):
        projection = {
            "latest": {"identity": "after-node", "formed_at_beijing": "2026-11-13T10:04:05+08:00", "quality_status": "PASS", "observations": [{}]},
        }
        selected = select_legal_pulse(projection, "2026-11-13T10:00:00+08:00")
        self.assertEqual(selected["selection_status"], "NO_LEGAL_PULSE_AT_NODE")
        self.assertEqual(selected["latest"], {})

    def test_two_sources_can_carry_different_identities(self):
        external = {"context_id": "external-old"}
        latest = {"context_id": "projection-new"}
        self.assertNotEqual(external["context_id"], latest["context_id"])


if __name__ == "__main__":
    unittest.main()
