from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

try:
    from market_data_guard import validate_market_row
except ModuleNotFoundError:
    from scripts.market_data_guard import validate_market_row

BEIJING = ZoneInfo("Asia/Shanghai")
REQUEST_TYPE = "EMERGENCY_EXTERNAL_MARKET_EVIDENCE"
SOURCE = "CHATGPT_WEB_DIRECT_PROVIDER"
INGRESS_PREFIX = "requests/live_snapshot/"
REQUIRED_ROW_FIELDS = ("symbol", "name", "provider", "provider_source_url", "provider_as_of_beijing", "market_phase", "close")


def _parse(value):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING)
        return parsed.astimezone(BEIJING)
    except (TypeError, ValueError):
        return None


def _relative(path):
    return str(path or "").replace("\\", "/").lstrip("./")


def _provider_base(value):
    return str(value or "").strip().lower().split(":", 1)[0]


def _symbol_base(value):
    return str(value or "").strip().upper().split(".", 1)[0]


def _approved_hosts(provider_config, provider):
    entry = (provider_config.get("providers") or {}).get(provider) or {}
    return {str(host).lower().strip() for host in (entry.get("approved_direct_hosts") or []) if str(host).strip()}


def _provider_allowed(provider_config, symbol, provider):
    base = _provider_base(provider)
    values = []
    for key, candidates in (provider_config.get("objects") or {}).items():
        if _symbol_base(key) == _symbol_base(symbol):
            values.extend(candidates or [])
    return any(_provider_base(candidate) == base for candidate in values)


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_external_market_evidence(request, root, *, decision_time, availability_time, ingress_path=""):
    """Validate external evidence for the existing state-sync writer."""
    if str(request.get("request_type") or "").upper() != REQUEST_TYPE:
        raise ValueError("EXTERNAL_EVIDENCE_REQUEST_TYPE_REQUIRED")
    if str(request.get("source") or "").upper() != SOURCE:
        raise ValueError("EXTERNAL_EVIDENCE_SOURCE_REQUIRED")
    evidence_id = str(request.get("evidence_id") or request.get("request_id") or "").strip()
    if not evidence_id:
        raise ValueError("EXTERNAL_EVIDENCE_ID_REQUIRED")
    evidence_path = _relative(request.get("consumed_external_market_evidence"))
    actual_path = _relative(ingress_path)
    if not evidence_path.startswith(INGRESS_PREFIX) or not evidence_path.endswith(".json"):
        raise ValueError("EXTERNAL_EVIDENCE_PATH_INVALID")
    if actual_path and evidence_path != actual_path:
        raise ValueError("EXTERNAL_EVIDENCE_PATH_NOT_CURRENT_INGRESS")
    path = (root / evidence_path).resolve()
    if root.resolve() not in path.parents or not path.exists():
        raise ValueError("EXTERNAL_EVIDENCE_FILE_NOT_FOUND")
    market_date = str(request.get("market_date") or "").strip()
    market_phase = str(request.get("market_phase") or "").strip()
    retrieved_at = _parse(request.get("retrieved_at_beijing"))
    available_at = _parse(request.get("evidence_available_at_beijing") or request.get("persistence_available_at_beijing") or request.get("requested_at_beijing"))
    cutoff = _parse(decision_time)
    persistence_boundary = _parse(availability_time)
    if not market_date or not market_phase:
        raise ValueError("EXTERNAL_EVIDENCE_MARKET_CONTEXT_REQUIRED")
    if retrieved_at is None or available_at is None or cutoff is None or persistence_boundary is None:
        raise ValueError("EXTERNAL_EVIDENCE_CLOCKS_REQUIRED")
    if available_at > persistence_boundary:
        raise ValueError("EXTERNAL_EVIDENCE_NOT_AVAILABLE_BY_PERSISTENCE_BOUNDARY")
    if retrieved_at > persistence_boundary:
        raise ValueError("EXTERNAL_EVIDENCE_RETRIEVAL_AFTER_PERSISTENCE_BOUNDARY")
    critical = [str(item).strip() for item in (request.get("decision_critical_symbols") or []) if str(item).strip()]
    if not critical:
        raise ValueError("EXTERNAL_EVIDENCE_DECISION_CRITICAL_OBJECTS_REQUIRED")
    rows = request.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("EXTERNAL_EVIDENCE_ROWS_REQUIRED")
    by_symbol = {_symbol_base(row.get("symbol")): row for row in rows if isinstance(row, dict)}
    if any(_symbol_base(symbol) not in by_symbol for symbol in critical):
        raise ValueError("EXTERNAL_EVIDENCE_DECISION_CRITICAL_OBJECT_MISSING")
    provider_config = _load_json(root / "config" / "market" / "provider_priority.json")
    runtime_policy = _load_json(root / "config" / "runtime_policy.json")
    validated_rows = []
    for symbol in critical:
        row = by_symbol[_symbol_base(symbol)]
        missing = [field for field in REQUIRED_ROW_FIELDS if row.get(field) in (None, "")]
        if missing:
            raise ValueError(f"EXTERNAL_EVIDENCE_FIELD_MISSING:{_symbol_base(symbol)}:{','.join(missing)}")
        provider = str(row.get("provider") or "").strip()
        if not _provider_allowed(provider_config, row.get("symbol") or symbol, provider):
            raise ValueError(f"EXTERNAL_EVIDENCE_PROVIDER_NOT_REGISTERED:{_symbol_base(symbol)}:{provider}")
        host = (urlparse(str(row.get("provider_source_url") or "")).hostname or "").lower()
        if not host or host not in _approved_hosts(provider_config, _provider_base(provider)):
            raise ValueError(f"EXTERNAL_EVIDENCE_PROVIDER_HOST_INVALID:{_symbol_base(symbol)}:{host}")
        if str(row.get("source_type") or "DIRECT_PROVIDER").upper() != "DIRECT_PROVIDER":
            raise ValueError(f"EXTERNAL_EVIDENCE_SOURCE_NOT_DIRECT:{_symbol_base(symbol)}")
        if str(row.get("market_phase") or "") != market_phase:
            raise ValueError(f"EXTERNAL_EVIDENCE_PHASE_MISMATCH:{_symbol_base(symbol)}")
        provider_time = _parse(row.get("provider_as_of_beijing"))
        if provider_time is None or provider_time > cutoff:
            raise ValueError(f"EXTERNAL_EVIDENCE_PROVIDER_TIME_AFTER_CUTOFF:{_symbol_base(symbol)}")
        if provider_time > retrieved_at:
            raise ValueError(f"EXTERNAL_EVIDENCE_PROVIDER_TIME_AFTER_RETRIEVAL:{_symbol_base(symbol)}")
        normalized = dict(row)
        normalized.setdefault("provider_timestamp", int(provider_time.timestamp() * 1000))
        normalized.setdefault("quality_status", "PASS")
        valid, reason = validate_market_row(
            normalized, str(row.get("symbol") or symbol), str(row.get("name") or ""),
            market_date=market_date, now=retrieved_at, runtime_policy=runtime_policy,
            direct_only=True, allow_auction_partial=True,
        )
        if not valid:
            raise ValueError(f"EXTERNAL_EVIDENCE_ROW_INVALID:{_symbol_base(symbol)}:{reason}")
        validated_rows.append(normalized)
    return {
        "evidence_id": evidence_id, "path": evidence_path, "market_date": market_date,
        "market_phase": market_phase, "retrieved_at_beijing": retrieved_at.isoformat(),
        "available_at_beijing": available_at.isoformat(), "rows": validated_rows,
        "validation_status": "PASS",
    }
