from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "ETF规则_MASTER.md"
MONITOR_CONFIG = ROOT / "config" / "market" / "market_monitor_config.json"
EXPECTED_INDICES = {"000001.SH", "399006.SZ", "000688.SH", "NDX", "SOX", "N225", "KOSPI", "TWII", "HSTECH"}


def _master_index_section(text: str) -> str:
    start = text.find("第一层指数监测的正式对象为：")
    if start < 0:
        # The concise declaration in section 2 is a fallback only; section 6.3 is
        # the canonical detailed declaration that should normally be present.
        start = text.find("第一层正式指数为")
    if start < 0:
        return ""
    end_candidates = [
        p for p in (
            text.find("\n\n", start),
            text.find("第二层ETF", start),
            text.find("数据源优先使用直接指数", start),
        ) if p > start
    ]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end]


def _master_indices(text: str) -> set[str]:
    section = _master_index_section(text)
    local_names = {
        "000001.SH": "上证指数",
        "399006.SZ": "创业板指",
        "000688.SH": "科创50指数",
    }
    found = {code for code, name in local_names.items() if name in section or code in section}
    found |= {code for code in EXPECTED_INDICES if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])", section)}
    return found


class MasterFormalIndexContractTests(unittest.TestCase):
    def test_master_matches_formal_monitor_config_and_expected_audit_set(self):
        master_text = MASTER.read_text(encoding="utf-8")
        cfg = json.loads(MONITOR_CONFIG.read_text(encoding="utf-8"))
        formal = set(cfg["formal_index_layer"]["required_objects"])
        master = _master_indices(master_text)

        self.assertEqual(
            formal,
            EXPECTED_INDICES,
            msg=f"machine formal index layer drifted from audit set: formal={sorted(formal)} expected={sorted(EXPECTED_INDICES)}",
        )
        self.assertEqual(
            master,
            formal,
            msg=(
                "MASTER formal index declaration drifted from machine formal layer: "
                f"master={sorted(master)} formal={sorted(formal)} "
                f"missing_from_master={sorted(formal-master)} extra_in_master={sorted(master-formal)}"
            ),
        )

    def test_known_regression_missing_star50_is_detected(self):
        master_text = MASTER.read_text(encoding="utf-8")
        # Simulate the historical defect, removing both the formal-list token and
        # any descriptive mention inside the same canonical paragraph. The test is
        # about the detector's ability to reject a MASTER that no longer declares
        # STAR50, not about punctuation around one particular sentence.
        broken = master_text.replace("科创50指数", "已删除指数").replace("000688.SH", "REMOVED.STAR50")
        self.assertNotIn("000688.SH", _master_indices(broken))
        self.assertNotEqual(_master_indices(broken), EXPECTED_INDICES)


if __name__ == "__main__":
    unittest.main()
