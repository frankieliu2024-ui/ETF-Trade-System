from scripts.multi_source_market import normalize_chart, validate_rows


def test_quality_validator_accepts_valid_rows():
    rows = [
        {"date": "2026-08-21", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        {"date": "2026-08-22", "open": 10.5, "high": 12, "low": 10, "close": 11, "volume": 120},
    ]
    assert validate_rows(rows)["pass"] is True


def test_quality_validator_degrades_missing_required_fields():
    rows = [{"date": "2026-08-21", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": None}]
    assert validate_rows(rows)["pass"] is False
    assert validate_rows(rows)["missing_required_rows"] == 1


def test_quality_validator_rejects_bad_ohlc():
    rows = [{"date": "2026-08-21", "open": 10, "high": 9, "low": 8, "close": 8.5, "volume": 100}]
    assert validate_rows(rows)["pass"] is False
    assert validate_rows(rows)["bad_ohlc_rows"] == 1


def test_normalize_chart_keeps_unadjusted_and_timezone():
    payload = {
        "chart": {"result": [{"meta": {"timezone": "America/New_York"}, "timestamp": [1787261400], "indicators": {"quote": [{"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [10]}]}}]}
    }
    result = normalize_chart(payload, "TEST")
    assert result["adjusted"] is False
    assert result["timezone"] == "America/New_York"
    assert result["quality"]["pass"] is True
