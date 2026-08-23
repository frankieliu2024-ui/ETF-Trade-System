from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

try:
    from state_manager import atomic_json_write, now_utc, read_current
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc, read_current

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rows_by_symbol(snapshot: dict) -> dict[str, dict]:
    return {str(row.get("symbol")): row for row in snapshot.get("rows", []) if row.get("symbol")}


def safe_delta(current: object, previous: object) -> float | None:
    if current is None or previous is None:
        return None
    try:
        return float(current) - float(previous)
    except (TypeError, ValueError):
        return None


def safe_pct(current: object, previous: object) -> float | None:
    if current is None or previous in (None, 0, 0.0):
        return None
    try:
        return (float(current) / float(previous) - 1.0) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def build(root: Path = ROOT) -> dict:
    current = read_current(root)
    market_date = current.get("market_date", "")
    snapshot_dir = root / "data" / "market" / "snapshots"
    candidates: list[tuple[Path, dict]] = []

    if snapshot_dir.exists() and market_date:
        for path in sorted(snapshot_dir.glob(f"{market_date}_*.json")):
            try:
                obj = load_json(path)
            except Exception:
                continue
            if obj.get("market_date") == market_date and obj.get("quality_status") == "PASS":
                candidates.append((path, obj))

    base = {
        "generated_at": now_utc(),
        "market_date": market_date,
        "mode": "OBJECTIVE_INTRADAY_DELTA",
        "read_only": True,
        "decision_boundary": "仅记录相邻有效行情脉冲变化，不生成机会、风险许可、金额或买卖动作。",
    }

    if len(candidates) < 2:
        return {
            **base,
            "status": "INSUFFICIENT_HISTORY",
            "previous_snapshot": "",
            "latest_snapshot": current.get("latest_snapshot", ""),
            "interval_seconds": None,
            "changes": [],
        }

    previous_path, previous = candidates[-2]
    latest_path, latest = candidates[-1]
    previous_rows = rows_by_symbol(previous)
    latest_rows = rows_by_symbol(latest)

    try:
        t0 = datetime.fromisoformat(str(previous.get("captured_at", "")))
        t1 = datetime.fromisoformat(str(latest.get("captured_at", "")))
        interval_seconds = int((t1 - t0).total_seconds())
    except Exception:
        interval_seconds = None

    changes = []
    for symbol in sorted(set(previous_rows) | set(latest_rows)):
        before = previous_rows.get(symbol, {})
        after = latest_rows.get(symbol, {})
        changes.append({
            "symbol": symbol,
            "asset_class": after.get("asset_class") or before.get("asset_class", ""),
            "previous_close": before.get("close"),
            "latest_close": after.get("close"),
            "price_change_abs": safe_delta(after.get("close"), before.get("close")),
            "price_change_pct": safe_pct(after.get("close"), before.get("close")),
            "previous_high": before.get("high"),
            "latest_high": after.get("high"),
            "previous_low": before.get("low"),
            "latest_low": after.get("low"),
            "volume_delta": safe_delta(after.get("volume"), before.get("volume")),
            "amount_delta": safe_delta(after.get("amount"), before.get("amount")),
            "quality_status": after.get("quality_status", "MISSING"),
        })

    return {
        **base,
        "status": "READY",
        "previous_snapshot": str(previous_path.relative_to(root)).replace("\\", "/"),
        "latest_snapshot": str(latest_path.relative_to(root)).replace("\\", "/"),
        "previous_captured_at": previous.get("captured_at", ""),
        "latest_captured_at": latest.get("captured_at", ""),
        "interval_seconds": interval_seconds,
        "changes": changes,
    }


def main() -> None:
    result = build(ROOT)
    target = ROOT / "data" / "state" / "market_delta.json"
    atomic_json_write(target, result)
    print(json.dumps({
        "ok": True,
        "status": result.get("status"),
        "market_date": result.get("market_date"),
        "count": len(result.get("changes", [])),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
