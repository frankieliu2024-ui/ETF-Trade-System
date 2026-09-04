from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_production_acceptance import persist_consistency_report_if_valid


class ProductionAcceptanceConsistencyPersistenceTests(unittest.TestCase):
    def test_invalid_report_does_not_overwrite_existing_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "system_consistency.json"
            existing = {
                "status": "WARNING",
                "hard_error_count": 0,
                "checks": [{"name": "existing", "status": "PASS"}],
            }
            target.write_text(json.dumps(existing), encoding="utf-8")

            self.assertFalse(persist_consistency_report_if_valid({}, target))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), existing)

    def test_valid_report_replaces_existing_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "system_consistency.json"
            target.write_text("{}", encoding="utf-8")
            fresh = {
                "status": "PASS",
                "hard_error_count": 0,
                "checks": [{"name": "fresh", "status": "PASS"}],
            }

            self.assertTrue(persist_consistency_report_if_valid(fresh, target))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), fresh)

    def test_structurally_incomplete_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "system_consistency.json"
            target.write_text('{"sentinel": true}', encoding="utf-8")
            incomplete = {"status": "PASS", "hard_error_count": 0}

            self.assertFalse(persist_consistency_report_if_valid(incomplete, target))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"sentinel": True})


if __name__ == "__main__":
    unittest.main()
