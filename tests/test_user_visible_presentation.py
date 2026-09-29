from __future__ import annotations

import unittest

from scripts.user_visible_presentation import (\n    validate_formal_decision_reply_nonblocking,\n    validate_user_visible_content,\n)


SECURITIES = {
    "159981": "能源化工ETF",
    "561980": "半导体设备ETF",
    "159992": "创新药ETF",
}


class UserVisiblePresentationContractTests(unittest.TestCase):
    def test_rejects_bare_known_security_code(self):
        with self.assertRaisesRegex(ValueError, "创新药ETF"):
            validate_user_visible_content("159992已成交。", security_map=SECURITIES)

    def test_rejects_slash_joined_bare_codes(self):
        with self.assertRaisesRegex(ValueError, "能源化工ETF"):
            validate_user_visible_content("159981/561980仍有历史归因。", security_map=SECURITIES)

    def test_accepts_canonical_security_display_identity(self):
        validate_user_visible_content(
            "能源化工ETF（159981）/半导体设备ETF（561980）仍有历史归因；创新药ETF（159992）已进入持仓。",
            security_map=SECURITIES,
        )

    def test_rejects_closure_before_canonical_acceptance(self):
        with self.assertRaisesRegex(ValueError, "canonical persistence"):
            validate_user_visible_content("【执行状态】本事项已闭环", security_map=SECURITIES)

    def test_accepts_closure_only_when_explicitly_confirmed(self):
        validate_user_visible_content(
            "【执行状态】本事项已闭环\n\n本事项结束，不再追加维护。",
            security_map=SECURITIES,
            canonical_closure_confirmed=True,
        )

    def test_rejects_internal_identity_in_normal_business_mode(self):
        with self.assertRaisesRegex(ValueError, "internal identity"):
            validate_user_visible_content("request_id=abc123", security_map=SECURITIES)

    def test_allows_internal_identity_in_explicit_technical_audit_mode(self):
        validate_user_visible_content(
            "request_id=abc123；commit SHA=deadbeef",
            security_map=SECURITIES,
            technical_audit_mode=True,
        )

    def test_does_not_reject_unrelated_six_digit_values(self):
        validate_user_visible_content(
            "本次样本编号123456，金额123456元；数据日期2026-09-29。",
            security_map=SECURITIES,
        )


if __name__ == "__main__":
    unittest.main()
