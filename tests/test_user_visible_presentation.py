from __future__ import annotations

import unittest

from scripts.user_visible_presentation import (
    build_formal_decision_presentation_binding,
    validate_formal_decision_reply_nonblocking,
    validate_user_visible_content,
)


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



    def test_rejects_evidenced_control_plane_jargon_in_normal_business_mode(self):
        samples = (
            "本次 request-bound 正式事实已经 READY，Discovery 亦 READY，没有动作级 blocker。",
            "持仓ETF追加全部为 NO_ADD。",
            "BUSINESS_DECISION_READY，reply_freezable=true。",
            "canonical persistence 尚在后续处理。",
            "Observation EXIT。",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                with self.assertRaisesRegex(ValueError, "business language"):
                    validate_user_visible_content(sample, security_map=SECURITIES)

    def test_accepts_plain_business_equivalents(self):
        samples = (
            "本次判断所需的账户、行情和全市场机会数据均已满足决策要求，目前没有影响判断的关键数据缺口。",
            "现有ETF持仓本次均不建议追加资金。",
            "本次判断已经形成，但相关正式状态更新尚未全部确认。",
            "本次判断认为应移出观察范围。",
            "最新行情截至13:05，满足本次判断的时效要求。",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                validate_user_visible_content(sample, security_map=SECURITIES)

    def test_allows_control_plane_jargon_in_explicit_technical_audit_mode(self):
        validate_user_visible_content(
            "request-bound READY；Discovery READY；NO_ADD；Observation EXIT；canonical persistence",
            security_map=SECURITIES,
            technical_audit_mode=True,
        )

    def test_formal_reply_presentation_failure_never_revokes_business_ready(self):
        result = validate_formal_decision_reply_nonblocking(
            "159992已成交。",
            security_map=SECURITIES,
        )
        self.assertTrue(result["business_reply_eligible"])
        self.assertFalse(result["presentation_valid"])
        self.assertIn("创新药ETF", result["presentation_error"])

    def test_formal_reply_closure_tense_failure_never_revokes_business_ready(self):
        result = validate_formal_decision_reply_nonblocking(
            "【执行状态】本事项已闭环",
            security_map=SECURITIES,
            canonical_closure_confirmed=False,
        )
        self.assertTrue(result["business_reply_eligible"])
        self.assertFalse(result["presentation_valid"])

    def test_formal_reply_valid_payload_remains_immediately_eligible(self):
        result = validate_formal_decision_reply_nonblocking(
            "创新药ETF（159992）继续持有。",
            security_map=SECURITIES,
        )
        self.assertEqual(
            result,
            {
                "business_reply_eligible": True,
                "presentation_valid": True,
                "presentation_error": "",
            },
        )


if __name__ == "__main__":
    unittest.main()


    def test_formal_decision_binding_fingerprints_checked_text(self):
        binding = build_formal_decision_presentation_binding(
            "【业务结论】保持现金。",
            request_id="formal-test-request",
            decision_id="formal-test-decision",
        )
        self.assertTrue(binding["presentation_valid"])
        self.assertEqual(binding["request_id"], "formal-test-request")
        self.assertEqual(binding["decision_id"], "formal-test-decision")
        self.assertEqual(len(binding["content_sha256"]), 64)

    def test_formal_decision_binding_preserves_business_eligibility_on_noise(self):
        binding = build_formal_decision_presentation_binding(
            "READY：request_id=internal",
            request_id="formal-test-request",
            decision_id="formal-test-decision",
        )
        self.assertTrue(binding["business_reply_eligible"])
        self.assertFalse(binding["presentation_valid"])
