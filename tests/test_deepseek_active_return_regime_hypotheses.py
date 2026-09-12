from types import SimpleNamespace

import pytest

from scripts.run_deepseek_active_return_regime_hypotheses import (
    BLIND_HOLDOUT_END,
    BLIND_HOLDOUT_START,
    DISCOVERY_END,
    build_discovery_input,
    validate_hypothesis_output,
)


def _active_fixture():
    block = {
        "5": {"folds": {"fold1": {"mean": -0.01}, "fold2": {"mean": 0.02}, "fold3": {"mean": 9.99}}},
        "10": {"folds": {"fold1": {"mean": -0.02}, "fold2": {"mean": 0.03}, "fold3": {"mean": 8.88}}},
    }
    return {
        "method": {"entry": "next open"},
        "active_migration_spread": block,
        "right_tail_holding": block,
        "leadership_continuation": block,
    }


def _alphagen_fixture():
    return {
        "fold_results": [
            {
                "horizon_trading_days": 5,
                "test_segment": ["2025-04-01", "2025-09-30"],
                "alphagen": {"mean_rank_ic": 0.1},
                "baselines": {"ret_20d_pct": {"mean_rank_ic": 0.09}},
            },
            {
                "horizon_trading_days": 5,
                "test_segment": ["2025-10-01", "2026-03-31"],
                "alphagen": {"mean_rank_ic": 99.0},
                "baselines": {},
            },
        ]
    }


def test_discovery_payload_withholds_2026_outcomes():
    result = build_discovery_input(_active_fixture(), _alphagen_fixture())
    encoded = str(result)
    assert result["discovery_window"]["end"] == DISCOVERY_END
    assert result["blind_holdout"]["start"] == BLIND_HOLDOUT_START
    assert result["blind_holdout"]["end"] == BLIND_HOLDOUT_END
    assert "9.99" not in encoded
    assert "8.88" not in encoded
    assert "99.0" not in encoded
    assert len(result["existing_formulaic_alpha_search"]["discovery_fold_results"]) == 1


def test_validator_accepts_only_interpretable_allowlisted_regime_rules():
    cfg = SimpleNamespace(provider="deepseek", model="deepseek-chat")
    raw = {
        "hypotheses": [{
            "hypothesis_id": "breadth_filter_1",
            "base_evidence": "active_migration_spread",
            "conditions": [
                {"feature": "breadth_mom20_positive_ratio", "op": "gte", "threshold": 0.6},
                {"feature": "median_20d_drawdown_pct", "op": "gte", "threshold": -5.0},
            ],
            "horizons_trading_days": [10, 20],
            "economic_logic": "Broad participation may improve persistence.",
            "failure_regime": "Narrow leadership.",
            "falsification": "Reject if discovery or blind holdout net spread is not positive/stable.",
        }],
        "uncertainties": ["Small ETF universe."],
    }
    out = validate_hypothesis_output(raw, cfg)
    assert out["status"] == "OK"
    assert out["production_action"] is None
    assert out["formal_state_write"] is False
    assert out["result"]["trade_signal"] is None
    assert out["result"]["production_integration"] is False


@pytest.mark.parametrize("feature", ["future_return_5d", "asset_code", "llm_score"])
def test_validator_rejects_non_allowlisted_features(feature):
    cfg = SimpleNamespace(provider="deepseek", model="deepseek-chat")
    raw = {
        "hypotheses": [{
            "hypothesis_id": "bad",
            "base_evidence": "right_tail_holding",
            "conditions": [{"feature": feature, "op": "gt", "threshold": 0.0}],
            "horizons_trading_days": [5],
            "economic_logic": "x",
            "failure_regime": "y",
            "falsification": "z",
        }],
        "uncertainties": [],
    }
    with pytest.raises(ValueError):
        validate_hypothesis_output(raw, cfg)


def test_validator_rejects_trade_or_extra_fields_by_exact_schema():
    cfg = SimpleNamespace(provider="deepseek", model="deepseek-chat")
    raw = {
        "hypotheses": [{
            "hypothesis_id": "bad_extra",
            "base_evidence": "right_tail_holding",
            "conditions": [{"feature": "median_mom20_pct", "op": "gt", "threshold": 0.0}],
            "horizons_trading_days": [5],
            "economic_logic": "x",
            "failure_regime": "y",
            "falsification": "z",
            "buy": True,
        }],
        "uncertainties": [],
    }
    with pytest.raises(ValueError):
        validate_hypothesis_output(raw, cfg)
