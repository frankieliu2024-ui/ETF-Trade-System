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

ETF_CODES = {
    "561980", "588000", "159781", "159941", "159561", "513520", "513180", "518880",
    "159992", "515880", "159326",
}


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


def looks_like_etf(position: dict, code: str) -> bool:
    security_type = str(first(position, "security_type", "asset_type", "type", "instrument_type") or "").upper()
    name = str(first(position, "name", "security_name", "instrument_name") or "")
    return code in ETF_CODES or "ETF" in security_type or "ETF" in name.upper()


def build() -> dict:
    account = read_account_fact(ROOT)
    policy = read_json(POLICY_PATH, {})
    role_memory = read_json(ROLE_PATH, {"roles": {}})
    roles = role_memory.get("roles", {}) if isinstance(role_memory, dict) else {}

    ipo_base_stocks = []
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
        if looks_like_etf(position, code):
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
        if item["role"] == "IPO_BASE_STOCK" and item["role_status"] == "CONFIRMED":
            ipo_base_stocks.append(item)
        else:
            unclassified_stocks.append(item)

    return {
        "generated_at": now_utc(),
        "account_fact_status": account.get("status", "MISSING"),
        "account_updated_at": account.get("updated_at", ""),
        "default_stock_layer": {
            "mode": "DYNAMIC_FROM_CURRENT_ACCOUNT",
            "ipo_base_stocks": ipo_base_stocks,
            "unclassified_stocks": unclassified_stocks,
            "detected_non_etf_stocks": detected_non_etf_stocks,
            "rule": "默认个股监测只来自当前账户实际持有的非ETF个股。已确认IPO_BASE_STOCK进入打新底仓监测；首次出现且角色未确认的个股只提示一次分类，不猜测用途。数量归零后退出当前监测。",
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
        "unclassified_count": len(context["default_stock_layer"]["unclassified_stocks"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
