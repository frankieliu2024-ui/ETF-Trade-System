from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


SURFACED_STATES = ("PERSISTENT_TREND", "TREND_CHANGE", "RECOVERY_BREAKOUT")


@dataclass(frozen=True)
class DiscoveryConfig:
    min_history: int = 65
    persistent_windows: tuple[int, ...] = (20, 40, 60)
    min_persistent_positive_windows: int = 2
    max_drawdown_60: float = 0.12
    trend_change_short: int = 10
    trend_change_long: int = 40
    trend_change_gap: float = 0.05
    breakout_window: int = 40
    breakout_tolerance: float = 0.015
    recovery_drawdown: float = 0.08
    recovery_fraction: float = 0.65


def _frame(rows: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "close"}
    missing = required - set(rows.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    out = rows.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["date", "close"]).sort_values("date")
    return out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)


def _ret(close: pd.Series, sessions: int) -> float | None:
    if len(close) <= sessions or close.iloc[-sessions - 1] <= 0:
        return None
    return float(close.iloc[-1] / close.iloc[-sessions - 1] - 1.0)


def _max_drawdown(close: pd.Series) -> float:
    running_max = close.cummax()
    drawdown = close / running_max - 1.0
    return abs(float(drawdown.min()))


def _persistent_trend(frame: pd.DataFrame, cfg: DiscoveryConfig) -> dict[str, Any] | None:
    close = frame["close"]
    returns = {w: _ret(close, w) for w in cfg.persistent_windows}
    positives = [w for w, value in returns.items() if value is not None and value > 0]
    drawdown = _max_drawdown(close.tail(max(cfg.persistent_windows) + 1))
    if len(positives) < cfg.min_persistent_positive_windows or drawdown > cfg.max_drawdown_60:
        return None
    return {
        "state": "PERSISTENT_TREND",
        "evidence": {
            "window_returns": {str(k): round(v, 6) if v is not None else None for k, v in returns.items()},
            "positive_windows": positives,
            "max_drawdown_60": round(drawdown, 6),
        },
    }


def _trend_change(frame: pd.DataFrame, cfg: DiscoveryConfig) -> dict[str, Any] | None:
    close = frame["close"]
    short_ret = _ret(close, cfg.trend_change_short)
    long_ret = _ret(close, cfg.trend_change_long)
    if short_ret is None or long_ret is None:
        return None
    long_per_short = long_ret * cfg.trend_change_short / cfg.trend_change_long
    gap = short_ret - long_per_short
    direction = "STRENGTHENING" if gap >= cfg.trend_change_gap else "WEAKENING" if gap <= -cfg.trend_change_gap else None
    if direction is None:
        return None
    return {
        "state": "TREND_CHANGE",
        "evidence": {
            "direction": direction,
            f"return_{cfg.trend_change_short}": round(short_ret, 6),
            f"return_{cfg.trend_change_long}": round(long_ret, 6),
            "short_vs_long_normalized_gap": round(gap, 6),
        },
    }


def _recovery_breakout(frame: pd.DataFrame, cfg: DiscoveryConfig) -> dict[str, Any] | None:
    close = frame["close"]
    window = close.tail(cfg.breakout_window + 1)
    if len(window) < cfg.breakout_window + 1:
        return None
    prior = window.iloc[:-1]
    current = float(window.iloc[-1])
    prior_high = float(prior.max())
    prior_low = float(prior.min())
    if prior_high <= 0:
        return None
    prior_drawdown = 1.0 - prior_low / prior_high
    recovered = (current - prior_low) / max(prior_high - prior_low, 1e-12)
    breakout = current >= prior_high * (1.0 - cfg.breakout_tolerance)
    if prior_drawdown < cfg.recovery_drawdown or recovered < cfg.recovery_fraction or not breakout:
        return None
    return {
        "state": "RECOVERY_BREAKOUT",
        "evidence": {
            "prior_drawdown": round(prior_drawdown, 6),
            "recovery_fraction": round(recovered, 6),
            "distance_to_prior_high": round(current / prior_high - 1.0, 6),
        },
    }


def discover_etf(
    rows: pd.DataFrame,
    *,
    code: str,
    name: str,
    formal_universe: set[str] | None = None,
    homogeneous_cluster: str | None = None,
    cfg: DiscoveryConfig | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Read-only V0 ETF discovery. It emits evidence, never a trade decision."""
    cfg = cfg or DiscoveryConfig()
    frame = _frame(rows)
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        frame = frame.loc[frame["date"] <= cutoff].reset_index(drop=True)
    observed_as_of = frame["date"].iloc[-1].date().isoformat() if not frame.empty else None
    base = {
        "code": str(code),
        "name": str(name),
        "as_of": observed_as_of,
        "requested_as_of": as_of,
        "research_only": True,
        "trade_signal": None,
        "decision_output_generated": False,
        "formal_universe_overlap": str(code) in (formal_universe or set()),
        "homogeneous_exposure_cluster": homogeneous_cluster,
        "decision_boundary": "WORTH_FULL_EVALUATION is research discovery only; existing MASTER remains the sole decision boundary.",
    }
    if len(frame) < cfg.min_history:
        return {**base, "status": "INSUFFICIENT_HISTORY", "surfaced_states": [], "worth_full_evaluation": False}

    states = [
        x for x in (
            _persistent_trend(frame, cfg),
            _trend_change(frame, cfg),
            _recovery_breakout(frame, cfg),
        )
        if x is not None
    ]
    amount_note = None
    if "amount" in frame and frame["amount"].notna().any():
        recent = frame["amount"].tail(20)
        amount_note = {"avg_amount_20": round(float(recent.mean()), 2)}
    return {
        **base,
        "status": "PASS",
        "surfaced_states": states,
        "liquidity_executability_note": amount_note,
        "worth_full_evaluation": bool(states),
        "discovery_semantic": "WORTH_FULL_EVALUATION" if states else None,
    }


def compress_homogeneous_exposure(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress exact declared exposure clusters without scoring or ranking.

    The first input object for a cluster is retained. Callers must provide a
    deterministic representative order based on separately auditable
    executability facts; this function never invents a hidden score.
    """
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for item in candidates:
        if not item.get("worth_full_evaluation"):
            continue
        cluster = item.get("homogeneous_exposure_cluster")
        if cluster:
            if cluster in seen:
                continue
            seen.add(cluster)
        output.append(item)
    return output
\n

def discover_state_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compress persistent states into PIT-safe information-change events.

    This is research-only. A delta event is not a management identity, lifecycle
    state, MASTER decision, or trade signal.
    """
    previous_states = set((previous or {}).get("surfaced_states") or [])
    current_states = set(current.get("surfaced_states") or [])
    entered = sorted(current_states - previous_states)
    exited = sorted(previous_states - current_states)
    return {
        "code": current.get("code"),
        "as_of": current.get("as_of"),
        "entered_states": entered,
        "exited_states": exited,
        "material_delta": bool(entered or exited),
        "worth_full_evaluation": bool(entered),
        "research_only": True,
        "management_identity_change": False,
        "trade_signal": None,
        "decision_output_generated": False,
    }


def route_discovery_result(
    discovery: dict[str, Any],
    *,
    holding_codes: set[str] | None = None,
    observation_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Route discovery evidence without creating a third formal ETF identity."""
    code = str(discovery.get("code"))
    holdings = {str(x) for x in (holding_codes or set())}
    observations = {str(x) for x in (observation_codes or set())}
    if code in holdings:
        route = "HOLDING_DELTA_EVIDENCE"
        full_evaluation = False
    elif code in observations:
        route = "OBSERVATION_DELTA_EVIDENCE"
        full_evaluation = False
    else:
        route = "EPHEMERAL_FULL_EVALUATION_INPUT"
        full_evaluation = bool(discovery.get("worth_full_evaluation"))
    return {
        "code": code,
        "route": route,
        "ephemeral": route == "EPHEMERAL_FULL_EVALUATION_INPUT",
        "full_evaluation_ingress": full_evaluation,
        "auto_promote_to_observation": False,
        "management_identity_change": False,
        "trade_signal": None,
        "decision_output_generated": False,
    }


def consolidate_discovery_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse same-day, same-state, same-exposure discovery events.

    Representative selection is deterministic and auditable:
    1. formal holding evidence is preserved first;
    2. then formal observation evidence;
    3. then out-of-pool ephemeral candidates;
    4. within the same route, higher recent average amount is preferred when
       both values are available; otherwise lexical code order breaks ties.

    This is exposure/event de-duplication, not a cross-opportunity score.
    Different states or exposure clusters remain separate.
    """
    route_priority = {
        "HOLDING_DELTA_EVIDENCE": 0,
        "OBSERVATION_DELTA_EVIDENCE": 1,
        "EPHEMERAL_FULL_EVALUATION_INPUT": 2,
    }

    def avg_amount(event: dict[str, Any]) -> float | None:
        note = event.get("liquidity_executability_note") or {}
        value = note.get("avg_amount_20")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def representative_key(event: dict[str, Any]) -> tuple:
        route = str(event.get("route") or "EPHEMERAL_FULL_EVALUATION_INPUT")
        amount = avg_amount(event)
        return (
            route_priority.get(route, 9),
            0 if amount is not None else 1,
            -(amount or 0.0),
            str(event.get("code") or ""),
        )

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for event in events:
        entered = list(event.get("entered_states") or [])
        cluster = event.get("homogeneous_exposure_cluster")
        as_of = str(event.get("as_of") or "")
        if not entered or not cluster:
            passthrough.append({**event, "consolidated_member_codes": [str(event.get("code"))]})
            continue
        for state in entered:
            groups.setdefault((as_of, str(state), str(cluster)), []).append(event)

    consolidated: list[dict[str, Any]] = []
    for (as_of, state, cluster), members in sorted(groups.items()):
        representative = min(members, key=representative_key)
        consolidated.append({
            **representative,
            "as_of": as_of,
            "entered_states": [state],
            "homogeneous_exposure_cluster": cluster,
            "consolidated_member_codes": sorted({str(x.get("code")) for x in members}),
            "consolidation_semantic": "SAME_DAY_SAME_STATE_SAME_EXPOSURE",
            "hidden_score": None,
            "trade_signal": None,
            "decision_output_generated": False,
        })
    return passthrough + consolidated
