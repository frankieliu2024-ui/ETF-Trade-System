import pytest

from scripts.business_decision_source import _normalize_holding_action, _parse_yuan_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HOLD", "HOLD"),
        ("hold", "HOLD"),
        ("持有", "HOLD"),
        ("持有管理", "HOLD"),
        ("持仓管理", "HOLD"),
        ("REDUCE", "REDUCE"),
        ("降低风险", "REDUCE"),
        ("EXIT", "EXIT"),
        ("退出", "EXIT"),
        ("全部退出", "EXIT"),
    ],
)
def test_equivalent_holding_action_representations_normalize(raw, expected) -> None:
    assert _normalize_holding_action(raw) == expected


@pytest.mark.parametrize("raw", [0, 0.0, "0", "0.00", "0元", "0.00元", "人民币0.00元"])
def test_equivalent_zero_yuan_representations_normalize(raw) -> None:
    assert _parse_yuan_amount(raw, "amount") == 0.0


@pytest.mark.parametrize("raw", ["HOLD_AND_EXIT", "观望", "0美元", "", None, True])
def test_unknown_or_ambiguous_representations_are_not_silently_reinterpreted(raw) -> None:
    if isinstance(raw, str) and raw in {"HOLD_AND_EXIT", "观望"}:
        assert _normalize_holding_action(raw) not in {"HOLD", "REDUCE", "EXIT"}
    else:
        with pytest.raises(ValueError):
            _parse_yuan_amount(raw, "amount")
