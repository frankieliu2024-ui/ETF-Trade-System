from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from scripts import market_notification_common as common


class MarketDivergencePresentationTests(unittest.TestCase):
    def event(self, *, code: str, title: str, content: str, object_codes: list[str]) -> dict:
        return {
            "event_type": "MARKET_VALUE_ALERT",
            "title": title,
            "content": content,
            "security_code": code,
            "security_name": "A股监测ETF横截面",
            "confirmation_context": {
                "market": "A_SHARE" if code.startswith("A_SHARE") else "US",
                "market_date": "2026-09-07",
                "direction": "DIVERGED",
                "event_category": "DIVERGENCE",
                "fact_family": "A_SHARE_CROSS_SECTION",
                "object_codes": object_codes,
                "source_fact_id": "2026-09-07:A_SHARE_ETF_DIVERGENCE:DIVERGENCE:DIVERGED:2026-09-07T11:32:18+08:00",
            },
        }

    @patch.object(common, "_user_visible_code_labels", return_value={"515880": "通信ETF（515880）", "515220": "煤炭ETF（515220）", "NDX": "纳斯达克100指数（NDX）", "SOX": "费城半导体指数（SOX）"})
    def test_a_share_divergence_uses_formal_names_and_hides_internal_token(self, _labels):
        event = self.event(code="A_SHARE_ETF_DIVERGENCE", title="【异动提醒】A_SHARE_ETF_DIVERGENCE出现显著分化", content="关键结构：领先515880 +4.85%，落后515220 -3.08%，差约7.93个百分点。", object_codes=["515880", "515220"])
        identity = copy.deepcopy(event["confirmation_context"])
        normalized = common._normalize_user_visible_event(event)
        self.assertIn("通信ETF（515880）+4.85%", normalized["content"])
        self.assertIn("煤炭ETF（515220）-3.08%", normalized["content"])
        self.assertIn("涨跌幅相差7.93个百分点", normalized["content"])
        self.assertNotIn("A_SHARE_ETF_DIVERGENCE", normalized["title"] + normalized["content"])
        self.assertNotIn("领先515880", normalized["content"])
        self.assertEqual(normalized["security_code"], "A_SHARE_ETF_DIVERGENCE")
        self.assertEqual(normalized["confirmation_context"], identity)

    @patch.object(common, "_user_visible_code_labels", return_value={"NDX": "纳斯达克100指数（NDX）", "SOX": "费城半导体指数（SOX）"})
    def test_overseas_divergence_keeps_structure_identity_but_hides_token(self, _labels):
        event = self.event(code="US_TECH_DIVERGENCE", title="【市场异动】US_TECH_DIVERGENCE出现显著分化", content="对象/结构：美股科技内部结构；横截面：NDX与SOX。", object_codes=["NDX", "SOX"])
        identity = copy.deepcopy(event["confirmation_context"])
        normalized = common._normalize_user_visible_event(event)
        self.assertNotIn("US_TECH_DIVERGENCE", normalized["title"] + normalized["content"])
        self.assertIn("纳斯达克100指数（NDX）", normalized["content"])
        self.assertIn("费城半导体指数（SOX）", normalized["content"])
        self.assertEqual(normalized["security_code"], "US_TECH_DIVERGENCE")
        self.assertEqual(normalized["confirmation_context"], identity)

    @patch.object(common, "_user_visible_code_labels", return_value={"515880": "通信ETF（515880）"})
    def test_single_etf_alert_is_normalized_without_touching_identity(self, _labels):
        event = self.event(code="515880", title="【市场异动】515880出现极端波动", content="对象：515880；当前涨跌：+3.20%。", object_codes=["515880"])
        identity = copy.deepcopy(event["confirmation_context"])
        normalized = common._normalize_user_visible_event(event)
        self.assertIn("通信ETF（515880）", normalized["title"] + normalized["content"])
        self.assertEqual(normalized["security_code"], "515880")
        self.assertEqual(normalized["confirmation_context"], identity)


if __name__ == "__main__":
    unittest.main()
