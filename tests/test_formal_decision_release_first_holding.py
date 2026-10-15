from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_formal_decision_uses_release_first_holding_policy():
    source = (ROOT / "scripts" / "business_decision_source.py").read_text(encoding="utf-8")
    context = (ROOT / "scripts" / "build_query_context.py").read_text(encoding="utf-8")
    protocol = (ROOT / "docs" / "Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")

    assert 'holding_capital_competition_policy' in context
    assert '"RELEASE_FIRST"' in context
    assert "HOLD is a conclusion" in source
    assert "holding_thesis_status" in source
    assert "long_term_outcome_review" in source
    assert "hold_over_release_reason" in source
    assert "opportunity_evidence" in source
    assert "buy_over_cash_reason" in source
    assert "execution_gate_reason" in source
    assert "释放优先审查" in protocol
    assert "不能单独构成HOLD理由" in protocol
    assert "买入与卖出采用对称证明责任" in protocol


def test_formal_decision_does_not_turn_release_first_into_auto_trading():
    protocol = (ROOT / "docs" / "Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
    assert "不构成自动卖出、自动清仓或证券订单授权" in protocol
