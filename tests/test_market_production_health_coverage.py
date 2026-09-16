from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import production_health_guard as health


class ProductionHealthCoverageTest(unittest.TestCase):
    def test_revalidation_patterns_include_all_normative_rule_sources(self):
        patterns = health.consistency_push_patterns()
        for path in (
            "ETF规则_MASTER.md",
            "ETF与市场监测数据接口使用规范.md",
            "docs/ETF主动通知体系.md",
            "docs/生产变更与并发写入协议_V1.0.md",
        ):
            self.assertIn(path, patterns)

    def test_state_only_advance_remains_covered(self):
        patterns = health.consistency_push_patterns()
        self.assertEqual(
            health.classify_consistency_coverage(
                "PASS",
                "validated",
                "current",
                ["data/state/e2e_status.json", "data/state/us_pulse_runtime_health.json"],
                patterns,
            ),
            ("PASS", "STATE_ONLY_ADVANCE", []),
        )

    def test_normative_rule_change_requires_revalidation(self):
        patterns = health.consistency_push_patterns()
        level, coverage, changed = health.classify_consistency_coverage(
            "PASS",
            "validated",
            "current",
            ["data/state/e2e_status.json", "docs/ETF主动通知体系.md"],
            patterns,
        )
        self.assertEqual(level, "ATTENTION")
        self.assertEqual(coverage, "REVALIDATION_REQUIRED")
        self.assertEqual(changed, ["docs/ETF主动通知体系.md"])

    def test_notification_state_uses_current_notifications_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notification_center.json"
            path.write_text(
                json.dumps({"schema_version": "2.2", "notifications": [{"id": "a"}, {"id": "b"}]}),
                encoding="utf-8",
            )
            state, status, detail, count = health.read_notification_state(path)
        self.assertEqual(status, "PASS")
        self.assertEqual(count, 2)
        self.assertEqual(len(state["notifications"]), 2)
        self.assertIn("schema_version=2.2", detail)

    def test_empty_notification_history_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notification_center.json"
            path.write_text(json.dumps({"schema_version": "2.2", "notifications": []}), encoding="utf-8")
            _, status, _, count = health.read_notification_state(path)
        self.assertEqual((status, count), ("PASS", 0))

    def test_missing_notification_state_is_explicit_attention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            _, status, detail, count = health.read_notification_state(path)
        self.assertEqual(status, "ATTENTION")
        self.assertEqual(count, 0)
        self.assertIn("state_unreadable=", detail)

    def test_malformed_notification_state_is_explicit_attention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notification_center.json"
            path.write_text("{", encoding="utf-8")
            _, status, detail, count = health.read_notification_state(path)
        self.assertEqual((status, detail, count), ("ATTENTION", "state_malformed_json", 0))

    def test_unusable_notification_collection_is_explicit_attention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notification_center.json"
            path.write_text(json.dumps({"schema_version": "2.2", "notifications": {}}), encoding="utf-8")
            _, status, detail, count = health.read_notification_state(path)
        self.assertEqual((status, detail, count), ("ATTENTION", "notifications_collection_unusable", 0))


if __name__ == "__main__":
    unittest.main()
