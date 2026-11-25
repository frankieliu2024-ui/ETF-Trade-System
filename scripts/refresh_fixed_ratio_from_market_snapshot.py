from __future__ import annotations

import json
import sys
from pathlib import Path

from fixed_ratio_position_metrics import refresh_fixed_ratio_metrics


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    account = load(root / "data/state/account_fact.json")
    current = load(root / "data/state/CURRENT.json")
    snapshot_path = root / str(current.get("latest_snapshot") or "")
    snapshot = load(snapshot_path)
    if str(snapshot.get("quality_status") or "").upper() not in {"PASS", "DEGRADED"}:
        raise SystemExit("latest market snapshot is not usable")
    rows = {
        str(row.get("symbol") or "").strip(): row
        for row in (snapshot.get("rows") or [])
        if isinstance(row, dict)
    }
    positions = []
    for original in account.get("positions") or []:
        position = dict(original)
        code = str(position.get("code") or "").strip()
        row = rows.get(code)
        if row and row.get("close") is not None:
            position["current_price"] = row["close"]
            position["last_price"] = row["close"]
        positions.append(position)
    market_account = dict(account)
    market_account["positions"] = positions
    market_account["updated_at"] = str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or account.get("updated_at") or "")
    market_account["source"] = "CANONICAL_MARKET_SNAPSHOT_FIXED_RATIO_REFRESH"
    refresh_fixed_ratio_metrics(root, market_account)


if __name__ == "__main__":
    main()
