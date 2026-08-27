from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
REPORT = ROOT / "data" / "state" / "overseas_runtime_health.json"


def parse_time(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    started = datetime.now(timezone.utc)
    context = load(ROOT / "data" / "state" / "overseas_context.json")
    us_context = load(ROOT / "data" / "state" / "us_extended_hours_context.json")
    now_bj = started.astimezone(BEIJING)
    objects = context.get("objects") or {}
    hard_errors = []
    warnings = []
    provider_as_of = {}
    object_health = {}

    for object_id, record in objects.items():
        latest = record.get("latest") or {}
        provider_as_of[object_id] = latest.get("as_of_beijing", "")
        object_health[object_id] = {
            "quality_status": record.get("quality_status", "MISSING"),
            "as_of_beijing": latest.get("as_of_beijing", ""),
            "market_date_local": latest.get("market_date_local", ""),
            "provider": record.get("provider", ""),
            "symbol": record.get("symbol", ""),
            "market_phase": record.get("market_phase_at_generation", ""),
            "freshness_status": record.get("freshness_status", ""),
            "delay_minutes": record.get("delay_minutes"),
            "error": record.get("error", ""),
        }

    for object_id in ("N225", "KOSPI"):
        record = objects.get(object_id) or {}
        latest = record.get("latest") or {}
        as_of = parse_time(latest.get("as_of_beijing", ""))
        same_day = bool(as_of and as_of.astimezone(BEIJING).date() == now_bj.date())
        valid = record.get("quality_status") in {"PASS", "DEGRADED"} and same_day
        if not valid:
            detail = f"{object_id}: quality={record.get('quality_status', 'MISSING')} as_of_beijing={latest.get('as_of_beijing', '') or 'MISSING'}"
            if now_bj.hour * 60 + now_bj.minute >= 8 * 60 + 20:
                if record.get("quality_status") in {"FAILED", "MISSING"}:
                    hard_errors.append(detail)
                else:
                    warnings.append(detail)
            else:
                warnings.append(detail)

    for object_id, record in objects.items():
        if object_id not in ("N225", "KOSPI") and record.get("quality_status") != "PASS":
            warnings.append(f"{object_id}: quality={record.get('quality_status', 'MISSING')} error={record.get('error', '')}")

    status = "FAIL" if hard_errors else ("DEGRADED" if warnings or context.get("quality_status") != "PASS" else "PASS")
    finished = datetime.now(timezone.utc)
    all_as_of = [parse_time(v) for v in provider_as_of.values() if parse_time(v)]
    top_as_of = max(all_as_of).astimezone(BEIJING).isoformat(timespec="seconds") if all_as_of else ""
    report = {
        "status": status,
        "pulse_success": not hard_errors and all((objects.get(k) or {}).get("quality_status") in {"PASS", "DEGRADED"} for k in ("N225", "KOSPI")),
        "hard_error_count": len(hard_errors),
        "warning_count": len(warnings),
        "hard_errors": hard_errors,
        "warnings": warnings,
        "scheduled_for": os.environ.get("SCHEDULED_FOR", os.environ.get("GITHUB_EVENT_NAME", "unknown")),
        "trigger": os.environ.get("GITHUB_EVENT_NAME", ""),
        "started_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "provider_as_of": top_as_of,
        "provider_as_of_by_object": provider_as_of,
        "finished_at": finished.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "commit_sha": os.environ.get("GITHUB_SHA", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "market_date_beijing": now_bj.date().isoformat(),
        "overseas_quality_status": context.get("quality_status", "MISSING"),
        "us_extended_hours_status": us_context.get("quality_status", "MISSING"),
        "objects": object_health,
        "provider_health": context.get("provider_health") or {},
        "rules": {
            "n225_kospi_same_day_required_after_beijing": "08:20",
            "provider_bar_time_is_authoritative": True,
            "stale_data_must_not_be_silent": True,
            "single_object_failure_is_degraded": True,
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Ownership boundary: the overseas pulse owns only overseas_runtime_health.
    # Generic A-share runtime_health is owned by the canonical A-share snapshot
    # producer family and must never be mutated by this builder, even transiently.
    print(json.dumps({"ok": not hard_errors, "status": status, "pulse_success": report["pulse_success"], "hard_error_count": len(hard_errors), "warning_count": len(warnings), "provider_as_of": top_as_of}, ensure_ascii=False))
    return 1 if hard_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
