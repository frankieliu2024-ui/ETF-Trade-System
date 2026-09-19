from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALID_ACTIONS = {"ADMIT", "RETAIN", "EXIT"}
MAX_OBSERVATION_ETFS = 12


def _code(value: Any) -> str:
    return str(value or "").upper().replace(".SH", "").replace(".SZ", "").strip()


def load_monitor_universe(root: Path) -> dict:
    return json.loads((root / "config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))


def held_etf_codes(root: Path, account: dict) -> set[str]:
    try:
        from build_stock_context import active_account_asset_codes
    except ModuleNotFoundError:
        from scripts.build_stock_context import active_account_asset_codes
    return {_code(x) for x in active_account_asset_codes(root, account).get("etf", set())}


def validate_observation_management(decision: dict, *, held_codes: set[str], monitored_codes: set[str], require_existing_coverage: bool = False) -> list[dict]:
    raw = decision.get("observation_management")
    existing_observations = {_code(x) for x in monitored_codes} - {_code(x) for x in held_codes}
    if raw in (None, []):
        if require_existing_coverage and existing_observations:
            raise ValueError("formal decision must RETAIN or EXIT every current observation ETF")
        return []
    if not isinstance(raw, list):
        raise ValueError("formal_decision.observation_management must be a list")
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("observation_management item must be an object")
        code = _code(item.get("code"))
        action = str(item.get("action") or "").upper().strip()
        if len(code) != 6 or not code.isdigit() or code in seen:
            raise ValueError("observation_management requires unique 6-digit ETF code")
        if action not in VALID_ACTIONS:
            raise ValueError(f"invalid observation action for {code}: {action}")
        if action == "ADMIT" and code in held_codes:
            raise ValueError(f"held ETF cannot be admitted as observation: {code}")
        if action == "ADMIT" and code in monitored_codes:
            raise ValueError(f"ADMIT requires a node-local non-managed ETF: {code}")
        if action in {"RETAIN", "EXIT"} and code not in monitored_codes:
            raise ValueError(f"{action} requires current continuous-monitor membership: {code}")
        if action in {"ADMIT", "RETAIN"}:
            required = ("name", "thscode", "thesis", "falsifier", "next_decision_information", "information_value_reason")
            missing = [key for key in required if not str(item.get(key) or "").strip()]
            if missing:
                raise ValueError(f"{action} {code} missing observation thesis fields: {','.join(missing)}")
        if action == "EXIT" and not str(item.get("reason") or "").strip():
            raise ValueError(f"EXIT {code} requires reason")
        out.append({**item, "code": code, "action": action})
        seen.add(code)
    if require_existing_coverage:
        covered_existing = {item["code"] for item in out if item["action"] in {"RETAIN", "EXIT"}}
        missing = sorted(existing_observations - covered_existing)
        if missing:
            raise ValueError("formal decision observation_management missing current observations: " + ", ".join(missing))
    return out


def project_monitor_universe(root: Path, account: dict, decision: dict) -> tuple[dict, bool]:
    universe = load_monitor_universe(root)
    objects = [dict(x) for x in (universe.get("objects") or [])]
    by_code = {_code(x.get("code")): x for x in objects}
    held = held_etf_codes(root, account)
    monitored = set(by_code)
    changes = validate_observation_management(decision, held_codes=held, monitored_codes=monitored)

    # Actual holdings are mandatory continuous-monitor objects. Existing metadata
    # is preserved; a missing held ETF must already be representable by account facts.
    account_positions = {_code(x.get("code")): x for x in (account.get("positions") or [])}
    for code in held:
        if code not in by_code:
            pos = account_positions.get(code) or {}
            thscode = str(pos.get("thscode") or "").strip()
            if not thscode:
                raise ValueError(f"held ETF {code} missing thscode; cannot mutate monitor universe safely")
            by_code[code] = {"code": code, "name": str(pos.get("name") or code), "thscode": thscode}

    for item in changes:
        code, action = item["code"], item["action"]
        if action == "ADMIT":
            by_code[code] = {
                "code": code,
                "name": str(item["name"]).strip(),
                "thscode": str(item["thscode"]).strip(),
            }
        elif action == "EXIT":
            if code in held:
                raise ValueError(f"held ETF cannot exit continuous monitor universe: {code}")
            by_code.pop(code, None)

    projected_observations = set(by_code) - held
    if len(projected_observations) > MAX_OBSERVATION_ETFS:
        raise ValueError(f"projected observation ETF count exceeds resource protection max {MAX_OBSERVATION_ETFS}")

    projected = {
        **universe,
        "objects": list(by_code.values()),
    }
    changed = projected != universe
    return projected, changed


def persist_monitor_universe(root: Path, account: dict, decision: dict) -> bool:
    projected, changed = project_monitor_universe(root, account, decision)
    if not changed:
        return False
    path = root / "config/market/etf_monitor_universe.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(projected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return True
