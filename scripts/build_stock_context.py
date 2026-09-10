from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_account_fact, read_json
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_account_fact, read_json

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
POLICY_PATH = ROOT / "config" / "market" / "stock_monitor_policy.json"
ROLE_PATH = ROOT / "data" / "state" / "asset_roles.json"
OUTPUT_PATH = ROOT / "data" / "state" / "stock_context.json"
ETF_UNIVERSE_PATH = ROOT / "config" / "market" / "etf_monitor_universe.json"


def load_etf_codes(root: Path | None = None) -> set[str]:
    universe_root = root or ROOT
    universe_path = universe_root / "config" / "market" / "etf_monitor_universe.json"
    if not universe_path.exists() and universe_root != ROOT:
        universe_path = ROOT / "config" / "market" / "etf_monitor_universe.json"
    universe = read_json(universe_path, {})
    codes = {
        str(item.get("code", "")).strip()
        for item in (universe.get("objects") or [])
        if isinstance(item, dict) and item.get("code")
    }
    if not codes:
        raise RuntimeError("canonical ETF universe is missing or empty")
    return codes


def first(position: dict, *keys: str):
    for key in keys:
        value = position.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_code(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def numeric(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def looks_like_etf(position: dict, code: str, etf_codes: set[str]) -> bool:
    security_type = str(first(position, "security_type", "asset_type", "type", "instrument_type") or "").upper()
    name = str(first(position, "name", "security_name", "instrument_name") or "")
    return code in etf_codes or "ETF" in security_type or "ETF" in name.upper()




def active_account_asset_codes(root: Path | None = None, account: dict | None = None) -> dict[str, set[str]]:
    """Return active membership using this builder's canonical ETF classifier."""
    root = root or ROOT
    account = account if account is not None else read_account_fact(root)
    etf_codes = load_etf_codes(root)
    membership = {"etf": set(), "stocks": set()}
    for position in account.get("positions") or []:
        if not isinstance(position, dict): continue
        code = normalize_code(first(position, "code", "symbol", "security_code", "instrument_code"))
        quantity = numeric(first(position, "quantity", "qty", "position", "shares", "volume"))
        if not code or quantity is None or quantity <= 0: continue
        membership["etf" if looks_like_etf(position, code, etf_codes) else "stocks"].add(code)
    return membership

def position_metric(position: dict, current_key: str, legacy_key: str, default: float = 0.0) -> float:
    """Read current account fields first, retaining explicit legacy compatibility."""
    value = first(position, current_key, legacy_key)
    parsed = numeric(value)
    return default if parsed is None else parsed


def build_managed_position_projection(root: Path | None = None, account: dict | None = None) -> dict:
    """Build the single managed-position set used by sell/lifecycle review.

    Every positive account position is managed.  ETF opportunity discovery remains
    a separate ETF-universe concern; non-ETF positions are never promoted to
    opportunity candidates by this projection.
    """
    root = root or ROOT
    account = account if account is not None else read_account_fact(root)
    etf_codes = load_etf_codes(root)
    positions = []
    seen_codes: set[str] = set()
    for position in account.get("positions") or []:
        if not isinstance(position, dict):
            continue
        code = normalize_code(first(position, "code", "symbol", "security_code", "instrument_code"))
        quantity = numeric(first(position, "quantity", "qty", "position", "shares", "volume"))
        if not code or quantity is None or quantity <= 0 or code in seen_codes:
            continue
        seen_codes.add(code)
        is_etf = looks_like_etf(position, code, etf_codes)
        positions.append({
            "code": code,
            "name": str(first(position, "name", "security_name", "instrument_name") or code),
            "quantity": quantity,
            "available_quantity": numeric(first(position, "available_quantity", "available_qty")),
            "asset_class": "ETF" if is_etf else "ACCOUNT_STOCK",
            "management_scope": "HELD_ETF" if is_etf else "ACCOUNT_STOCK",
            "role": str(first(position, "role", "asset_role") or ""),
            "origin": str(first(position, "origin", "origin_type", "source_origin") or ""),
            "current_price": position_metric(position, "current_price", "last_price"),
            "market_value": numeric(first(position, "market_value", "value", "position_value")),
            "pnl": position_metric(position, "pnl", "holding_pnl"),
            "pnl_pct": position_metric(position, "pnl_pct", "holding_pnl_pct"),
            "source": "account_fact.positions",
        })
    return {
        "status": str(account.get("status") or "MISSING"),
        "account_updated_at": str(account.get("updated_at") or ""),
        "positions": positions,
        "codes": [item["code"] for item in positions],
        "opportunity_scope": "ETF_UNIVERSE_ONLY",
        "decision_boundary": "仅用于当前持仓生命周期、持仓管理、降低风险、退出和资本效率比较；非ETF账户资产不得进入观察/Trial/Confirm候选。",
    }


def build() -> dict:
    account = read_account_fact(ROOT)
    etf_codes = load_etf_codes()
    policy = read_json(POLICY_PATH, {})
    role_memory = read_json(ROLE_PATH, {"roles": {}})
    roles = role_memory.get("roles", {}) if isinstance(role_memory, dict) else {}

    ipo_base_stocks = []
    ipo_allotment_stocks = []
    unclassified_stocks = []
    detected_non_etf_stocks = []

    for position in account.get("positions", []) or []:
        if not isinstance(position, dict):
            continue
        code = normalize_code(first(position, "code", "symbol", "security_code", "instrument_code"))
        if not code:
            continue
        quantity = numeric(first(position, "quantity", "qty", "position", "shares", "volume"))
        if quantity is not None and quantity <= 0:
            continue
        if looks_like_etf(position, code, etf_codes):
            continue

        item = {
            "code": code,
            "name": str(first(position, "name", "security_name", "instrument_name") or roles.get(code, {}).get("name", "")),
            "quantity": quantity,
            "market_value": numeric(first(position, "market_value", "value", "position_value")),
            "role": roles.get(code, {}).get("role", "UNCLASSIFIED_STOCK"),
            "role_status": roles.get(code, {}).get("status", "UNCONFIRMED"),
            "source": "account_fact.positions",
        }
        detected_non_etf_stocks.append(item)
        origin = str(first(position, "origin", "origin_type", "source_origin") or "").strip()
        origin_fact = str(first(position, "origin_fact", "origin_evidence", "source_fact") or "").strip()
        item["origin"] = origin
        item["origin_fact"] = origin_fact
        is_confirmed_ipo_base = item["role"] == "IPO_BASE_STOCK" and item["role_status"] == "CONFIRMED"
        is_confirmed_ipo_allotment = origin == "IPO_ALLOTMENT_ORIGIN"
        if is_confirmed_ipo_base:
            ipo_base_stocks.append(item)
        elif is_confirmed_ipo_allotment:
            ipo_allotment_stocks.append(item)
        else:
            unclassified_stocks.append(item)

    monitored_account_stocks = []
    seen_codes = set()
    for item in ipo_base_stocks + ipo_allotment_stocks:
        if item["code"] not in seen_codes:
            monitored_account_stocks.append(item)
            seen_codes.add(item["code"])

    return {
        "generated_at": now_utc(),
        "account_fact_status": account.get("status", "MISSING"),
        "account_updated_at": account.get("updated_at", ""),
        "default_stock_layer": {
            "mode": "DYNAMIC_FROM_CURRENT_ACCOUNT",
            "ipo_base_stocks": ipo_base_stocks,
            "ipo_allotment_stocks": ipo_allotment_stocks,
            "monitored_account_stocks": monitored_account_stocks,
            "unclassified_stocks": unclassified_stocks,
            "detected_non_etf_stocks": detected_non_etf_stocks,
            "rule": "默认个股监测只来自当前账户实际持有的非ETF个股。已确认IPO_BASE_STOCK及账户事实可追溯的IPO_ALLOTMENT_ORIGIN进入默认监测；真正未知来源的个股只提示一次分类，不猜测用途。数量归零后退出当前监测。",
        },
        "conditional_industry_observation": {
            "mode": "QUERY_TIME_DYNAMIC_DISCOVERY",
            "persistent_fixed_list": False,
            "markets": policy.get("conditional_industry_observation", {}).get("markets", []),
            "rule": policy.get("conditional_industry_observation", {}).get("selection_rule", ""),
            "decision_boundary": policy.get("conditional_industry_observation", {}).get("decision_boundary", ""),
            "time_alignment_required": True,
            "current_objects": [],
            "note": "产业链个股由当前ETF/行业假设临时发现和调用，不维护三星、海力士或任何A股/美股公司的固定日常名单。",
        },
        "needs_role_confirmation": bool(unclassified_stocks),
        "read_only": True,
    }


def main() -> None:
    context = build()
    atomic_json_write(OUTPUT_PATH, context)
    print(json.dumps({
        "ok": True,
        "account_fact_status": context["account_fact_status"],
        "ipo_base_count": len(context["default_stock_layer"]["ipo_base_stocks"]),
        "ipo_allotment_count": len(context["default_stock_layer"]["ipo_allotment_stocks"]),
        "monitored_account_stock_count": len(context["default_stock_layer"]["monitored_account_stocks"]),
        "unclassified_count": len(context["default_stock_layer"]["unclassified_stocks"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
