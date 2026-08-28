from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProviderDisplaySyncTest(unittest.TestCase):
    def test_formal_index_display_matches_provider_policy(self):
        monitor = json.loads((ROOT / "config/market/market_monitor_config.json").read_text(encoding="utf-8"))
        priority = json.loads((ROOT / "config/market/provider_priority.json").read_text(encoding="utf-8"))

        display_objects = {
            str(item.get("id")): item
            for item in (((monitor.get("classes") or {}).get("A") or {}).get("objects") or [])
            if isinstance(item, dict) and item.get("id")
        }
        policies = priority.get("object_fallback_policy") or {}
        formal_ids = set((monitor.get("formal_index_layer") or {}).get("required_objects") or [])

        checked = 0
        for object_id in sorted(formal_ids):
            policy = policies.get(object_id)
            if not isinstance(policy, dict) or not policy.get("primary"):
                continue
            self.assertIn(object_id, display_objects, f"missing formal-index display object: {object_id}")
            display = display_objects[object_id]
            self.assertEqual(
                str(display.get("preferred_source") or ""),
                str(policy.get("primary") or ""),
                f"preferred_source drift for {object_id}",
            )
            expected_fallback = [str(x) for x in (policy.get("fallback") or [])]
            actual_fallback = [x for x in str(display.get("fallback_source") or "").split("|") if x]
            self.assertEqual(actual_fallback, expected_fallback, f"fallback_source drift for {object_id}")
            checked += 1

        self.assertGreaterEqual(checked, 1, "no formal-index provider policies were checked")


if __name__ == "__main__":
    unittest.main()
