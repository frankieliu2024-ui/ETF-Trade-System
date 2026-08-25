from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

try:
    from state_manager import now_utc, read_account_fact, read_current, read_json
except ModuleNotFoundError:
    from scripts.state_manager import now_utc, read_account_fact, read_current, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
HISTORY_DIR = Path("events/research/daily_features")
VALIDATION_STATUS = Path("data/state/skfolio_research_status.json")
WINDOW = 120
CORR_THRESHOLD = 0.70
RELEASE_FRACTION = 0.02


def r4(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return round(x, 4) if math.isfinite(x) else None


def load_universe(root: Path) -> dict[str, str]:
    cfg = read_json(root / "config/market/etf_monitor_universe.json", {})
    return {str(x.get("code")): str(x.get("name")) for x in (cfg.get("objects") or []) if x.get("code")}


def load_close_history(root: Path, universe: dict[str, str]) -> tuple[list[str], dict[str, dict[str, float]]]:
    by_date: dict[str, dict[str, float]] = {}
    for path in sorted((root / HISTORY_DIR).glob("*.json")):
        obj = read_json(path, {})
        market_date = str(obj.get("market_date") or path.stem)
        rows = {}
        for item in obj.get("features") or []:
            code = str(item.get("code") or "")
            close = item.get("close")
            if code not in universe or close is None:
                continue
            try:
                rows[code] = float(close)
            except (TypeError, ValueError):
                continue
        if rows:
            by_date[market_date] = rows
    dates = sorted(by_date)
    return dates, by_date


def build_returns(dates: list[str], closes: dict[str, dict[str, float]], codes: list[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {c: {} for c in codes}
    prev: dict[str, float] = {}
    for d in dates:
        for code in codes:
            px = closes.get(d, {}).get(code)
            old = prev.get(code)
            if px is not None and old not in (None, 0.0):
                out[code][d] = px / old - 1.0
            if px is not None:
                prev[code] = px
    return out


def common_values(a: dict[str, float], b: dict[str, float], allowed_dates: set[str]) -> tuple[list[float], list[float]]:
    common = sorted((set(a) & set(b)) & allowed_dates)
    return [a[d] for d in common], [b[d] for d in common]


def covariance(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 20:
        return None
    x, y = xs[:n], ys[:n]
    mx, my = mean(x), mean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (n - 1)


def correlation(xs: list[float], ys: list[float]) -> float | None:
    cov = covariance(xs, ys)
    if cov is None:
        return None
    vx, vy = covariance(xs, xs), covariance(ys, ys)
    if vx is None or vy is None or vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def components(codes: list[str], corr: dict[tuple[str, str], float], threshold: float) -> list[list[str]]:
    parent = {c: c for c in codes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            value = corr.get((a, b))
            if value is not None and value >= threshold:
                union(a, b)
    groups: dict[str, list[str]] = defaultdict(list)
    for c in codes:
        groups[find(c)].append(c)
    return sorted([sorted(v) for v in groups.values() if len(v) >= 2], key=lambda z: (-len(z), z))


def normalized_account_weights(account: dict, eligible: set[str]) -> dict[str, float]:
    values = {}
    for p in account.get("positions") or []:
        code = str(p.get("code") or "")
        if p.get("asset_type") != "ETF" or code not in eligible:
            continue
        try:
            mv = float(p.get("market_value") or 0.0)
        except (TypeError, ValueError):
            continue
        if mv > 0:
            values[code] = mv
    total = sum(values.values())
    return {c: v / total for c, v in values.items()} if total > 0 else {}


def release_efficiency(weights: dict[str, float], returns: dict[str, dict[str, float]], window_dates: list[str]) -> dict[str, float]:
    codes = list(weights)
    if len(codes) < 2:
        return {}
    allowed = set(window_dates)
    cov = {}
    for i, a in enumerate(codes):
        for j, b in enumerate(codes):
            xs, ys = common_values(returns[a], returns[b], allowed)
            value = covariance(xs, ys)
            if value is None:
                return {}
            cov[(i, j)] = value * 252.0
    w = [weights[c] for c in codes]

    def variance(vec):
        return sum(vec[i] * cov[(i, j)] * vec[j] for i in range(len(codes)) for j in range(len(codes)))

    base_vol = math.sqrt(max(variance(w), 0.0))
    scores = {}
    for i, code in enumerate(codes):
        released = min(RELEASE_FRACTION, w[i])
        if released <= 1e-12:
            continue
        w2 = list(w)
        w2[i] -= released
        new_vol = math.sqrt(max(variance(w2), 0.0))
        scores[code] = (base_vol - new_vol) / released
    return scores


def display_component(comp: list[str], names: dict[str, str], account_weights: dict[str, float] | None = None) -> dict:
    result = {
        "codes": comp,
        "assets": [f"{names.get(c, c)}（{c}）" for c in comp],
    }
    if account_weights is not None:
        result["account_etf_weight_share"] = r4(sum(account_weights.get(c, 0.0) for c in comp))
    return result


def build(root: Path = ROOT) -> dict:
    validation = read_json(root / VALIDATION_STATUS, {})
    validation_ok = (
        validation.get("status") == "PASS"
        and validation.get("research_interpretation") == "PROMISING_FOR_RESEARCH_INTEGRATION"
        and validation.get("decision_eligible") is False
    )
    names = load_universe(root)
    dates, closes = load_close_history(root, names)
    current = read_current(root)
    account = read_account_fact(root)
    if not validation_ok:
        return {
            "schema_version": "1.0", "generated_at": now_utc(), "status": "BLOCKED",
            "reason": "skfolio_stage2_validation_not_approved", "decision_eligible": False,
            "trade_signal": None, "portfolio_target": None, "read_only": True,
        }
    if len(dates) < WINDOW + 1:
        return {
            "schema_version": "1.0", "generated_at": now_utc(), "status": "INSUFFICIENT_HISTORY",
            "risk_data_end": dates[-1] if dates else "", "decision_eligible": False,
            "trade_signal": None, "portfolio_target": None, "read_only": True,
        }

    window_dates = dates[-WINDOW:]
    returns = build_returns(dates, closes, list(names))
    eligible = [c for c in names if sum(d in returns[c] for d in window_dates) >= int(WINDOW * 0.85)]
    allowed = set(window_dates)
    corr_map: dict[tuple[str, str], float] = {}
    top_pairs = []
    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            xs, ys = common_values(returns[a], returns[b], allowed)
            value = correlation(xs, ys)
            if value is None:
                continue
            corr_map[(a, b)] = value
            top_pairs.append((value, a, b))
    top_pairs.sort(reverse=True)
    universe_components = components(eligible, corr_map, CORR_THRESHOLD)

    account_weights = normalized_account_weights(account, set(eligible)) if account.get("status") == "VALID" else {}
    holding_codes = list(account_weights)
    holding_corr = {(a, b): corr_map.get((a, b), corr_map.get((b, a))) for i, a in enumerate(holding_codes) for b in holding_codes[i + 1:]}
    holding_components = components(holding_codes, holding_corr, CORR_THRESHOLD) if len(holding_codes) >= 2 else []
    release = release_efficiency(account_weights, returns, window_dates) if account_weights else {}
    release_items = [
        {
            "code": code,
            "name": names.get(code, code),
            "display_name": f"{names.get(code, code)}（{code}）",
            "risk_side_release_efficiency": r4(score),
            "account_etf_weight": r4(account_weights.get(code)),
        }
        for code, score in sorted(release.items(), key=lambda z: z[1], reverse=True)
    ]

    risk_data_end = dates[-1]
    current_market_date = str(current.get("market_date") or "")
    alignment = "ALIGNED_WITH_CURRENT_MARKET_DATE" if risk_data_end == current_market_date else "LATEST_AVAILABLE_RESEARCH_DATE"
    account_status = account.get("status", "MISSING")
    use_now = bool(validation_ok and account_status == "VALID" and release_items)

    return {
        "schema_version": "1.0",
        "generated_at": now_utc(),
        "status": "READY" if use_now else "DEGRADED",
        "mode": "SKFOLIO_VALIDATED_RISK_EVIDENCE",
        "validation_basis": {
            "stage2_status": validation.get("status"),
            "stage2_interpretation": validation.get("research_interpretation"),
            "validated_methods": validation.get("research_candidates") or [],
            "upstream_commit": validation.get("upstream_commit"),
        },
        "methodology": {
            "risk_window_trading_days": WINDOW,
            "high_correlation_threshold": CORR_THRESHOLD,
            "marginal_release_fraction_of_etf_portfolio": RELEASE_FRACTION,
            "runtime_implementation": "stdlib implementation of Stage2-validated rolling correlation/covariance and marginal-volatility release evidence; no production optimizer dependency",
        },
        "risk_data_end": risk_data_end,
        "current_market_date": current_market_date,
        "data_alignment": alignment,
        "account_fact_status": account_status,
        "account_fact_updated_at": account.get("updated_at"),
        "eligible_etf_count": len(eligible),
        "universe_high_corr_components": [display_component(x, names) for x in universe_components],
        "top_correlated_pairs": [
            {
                "a": f"{names.get(a, a)}（{a}）",
                "b": f"{names.get(b, b)}（{b}）",
                "correlation": r4(v),
            }
            for v, a, b in top_pairs[:10]
        ],
        "current_account": {
            "etf_weights": {f"{names.get(c, c)}（{c}）": r4(w) for c, w in account_weights.items()},
            "high_corr_components": [display_component(x, names, account_weights) for x in holding_components],
            "risk_side_release_efficiency": release_items,
        },
        "use_in_current_decision": use_now,
        "interpretation_rule": "本证据只回答共同风险和风险侧边际资本释放效率；必须与机会假设、生命周期、风险收益、替代机会和资金去向共同判断。不得把风险侧释放排序解释为收益侧卖出顺序。",
        "capital_efficiency_rule": "不得生成综合资本效率总分，不得机械按风险贡献买卖；下一单位资本最有效用途仍由MASTER完整决策链决定。",
        "decision_eligible": False,
        "trade_signal": None,
        "portfolio_target": None,
        "trial_confirm": None,
        "master_override": False,
        "read_only": True,
    }


def main() -> None:
    print(json.dumps(build(ROOT), ensure_ascii=False))


if __name__ == "__main__":
    main()
