from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import build_market_regime_context as regime
    import minute_notification_context as notify
    from minute_context_production import acceptance_interpretation
except ModuleNotFoundError:
    from scripts import build_market_regime_context as regime
    from scripts import minute_notification_context as notify
    from scripts.minute_context_production import acceptance_interpretation


class MinuteContextFormalIntegrationTests(unittest.TestCase):
    def test_index_overlay_never_replaces_formal_price(self):
        item = {"price": 123.45, "change_pct": 1.2}
        feature = {
            "path_low": 120.0,
            "path_high": 125.0,
            "path_change_pct": 0.8,
            "recent_slope_pct_per_10m": 0.2,
            "recovery_from_path_low_pct": 2.0,
            "retreat_from_path_high_pct": -1.2,
            "path_low_as_of_beijing": "2026-08-28T10:00:00+08:00",
            "path_high_as_of_beijing": "2026-08-28T14:00:00+08:00",
            "latest_as_of_beijing": "2026-08-28T15:00:00+08:00",
            "sample_count": 242,
            "point_in_time": {"pass": True},
            "quote_alignment": {"pass": True},
            "production_selection_reason": "test",
        }
        regime._apply_minute_path(item, feature)
        self.assertEqual(item["price"], 123.45)
        self.assertEqual(item["minute_path_source"], "TENCENT_1M")
        self.assertEqual(item["sampling_coverage"], "HIGH")

    def test_acceptance_is_descriptive_not_trade_signal(self):
        evidence = {
            "status": "READY",
            "recent_10m": {"price_change_pct": 0.25},
            "comparison": {"amount_expansion_ratio": 1.4, "amount_relation": "EXPANDED_VS_PRIOR_10M", "recent_price_direction": "UP"},
        }
        text = acceptance_interpretation(evidence)
        self.assertIn("承接增强", text)
        self.assertNotIn("买入", text)
        self.assertNotIn("Confirm", text)

    def test_notification_uses_minute_context_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            structure = {
                "items": [{
                    "code": "561980",
                    "intraday_context": {
                        "path_low_as_of_beijing": "2026-08-28T13:42:00+08:00",
                        "path_high_as_of_beijing": "2026-08-28T10:01:00+08:00",
                        "extreme_sequence": "HIGH_THEN_LOW",
                    },
                    "turnover_acceptance_context": {
                        "recent_10m_marginal_acceptance": {
                            "status": "READY",
                            "recent_10m_price_change_pct": -0.3,
                            "amount_expansion_ratio": 1.2,
                            "interpretation_cn": "最近10分钟-0.30%、成交额为前10分钟1.20倍，回落且成交参与扩张，短线抛压更实。",
                        }
                    },
                }]
            }
            (state / "market_structure_context.json").write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
            with patch.object(notify, "STATE", state):
                note = notify.minute_notification_note("561980")
                enriched = notify.enrich_market_alert_content("## 发生了什么\n- x\n\n## 为什么重要\ny", "561980")
            self.assertIn("先高后低", note)
            self.assertIn("分钟级结构", enriched)
            self.assertIn("1.20倍", enriched)


if __name__ == "__main__":
    unittest.main()
