from scripts.build_query_context import _merge_discovery_candidate_quotes
from scripts.formal_etf_opportunity_discovery import attach_formal_quotes


def _q(symbol: str, source: str = "existing", quality: str = "PASS") -> dict:
    return {"symbol": symbol, "latest_price": 1.0, "quality_status": quality, "source": source}


def test_mixed_existing_and_refreshed_discovery_quotes_survive_together() -> None:
    codes = ["510001", "159002", "510003"]
    existing = {"quotes": [_q("510001.SH"), _q("159002.SZ")]}
    refreshed = {"quotes": [_q("510003.SH", "refresh")]}
    merged = _merge_discovery_candidate_quotes(codes, existing, refreshed)
    formal = {"candidates": [{"code": code} for code in codes]}
    attached = attach_formal_quotes(formal, merged)
    assert attached["formal_quote_coverage"] == 3
    assert [x["formal_quote_status"] for x in attached["candidates"]] == ["READY", "READY", "READY"]


def test_failed_missing_candidate_degrades_only_that_object() -> None:
    codes = ["510001", "159002", "510003"]
    existing = {"quotes": [_q("510001.SH"), _q("159002.SZ")]}
    refreshed = {"quotes": []}
    merged = _merge_discovery_candidate_quotes(codes, existing, refreshed)
    formal = {"candidates": [{"code": code} for code in codes]}
    attached = attach_formal_quotes(formal, merged)
    assert [x["formal_quote_status"] for x in attached["candidates"]] == ["READY", "READY", "UNAVAILABLE"]


def test_refreshed_quote_wins_same_symbol_without_duplicate() -> None:
    codes = ["510001"]
    existing = {"quotes": [_q("510001.SH", "existing")]}
    refreshed = {"quotes": [_q("510001.SH", "refresh")]}
    merged = _merge_discovery_candidate_quotes(codes, existing, refreshed)
    assert len(merged["quotes"]) == 1
    assert merged["quotes"][0]["source"] == "refresh"
