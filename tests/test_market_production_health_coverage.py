from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
