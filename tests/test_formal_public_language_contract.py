from scripts.user_visible_presentation import validate_user_visible_content


def test_execution_jargon_is_rejected_before_projection():
    content = "下一合法节点复核风险重建、持仓HOLD/REDUCE/EXIT、Discovery转化和现金机会成本。"
    try:
        validate_user_visible_content(content, technical_audit_mode=False, security_map={})
    except ValueError as exc:
        message = str(exc)
        assert "HOLD" in message
        assert "下一合法节点" in message
    else:
        raise AssertionError("execution jargon must not enter public presentation")


def test_handoff_status_is_rejected_from_business_body():
    content = "交接状态：PROJECTION_INCOMPLETE；通知状态：未确认。"
    try:
        validate_user_visible_content(content, technical_audit_mode=False, security_map={})
    except ValueError as exc:
        assert "交接状态" in str(exc)
    else:
        raise AssertionError("handoff diagnostics must not enter public presentation")
