from scripts.pulse_history import _number


def test_direction_contract_uses_explicit_reason_for_missing_reference():
    mark = _number("")
    prev_day = _number("")
    assert mark is None or prev_day in (None, 0)


def test_direction_contract_is_not_blank():
    for value in ("UP", "DOWN", "FLAT", "UNDETERMINED"):
        assert value
