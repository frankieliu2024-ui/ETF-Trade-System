from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "专项回测" / "outputs" / "v2215_live_snapshot"
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

ETF = [
    ("561980", "半导体设备ETF", "561980.SH"),
    ("588000", "科创50ETF", "588000.SH"),
    ("159781", "科创创业ETF", "159781.SZ"),
    ("159941", "纳指ETF", "159941.SZ"),
    ("159561", "德国ETF", "159561.SZ"),
    ("513520", "日经ETF", "513520.SH"),
    ("513180", "恒生科技ETF", "513180.SH"),
    ("518880", "黄金ETF", "518880.SH"),
]

INDEX = [
    ("000001", "上证指数", "000001.SH"),
    ("399006", "创业板指", "399006.SZ"),
]


def prepare_cli() -> str:
    node_bin = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin"
    if node_bin.exists():
        os.environ["PATH"] = str(node_bin) + os.pathsep + os.environ.get("PATH", "")
    found = shutil.which("hithink-finance")
    if found:
        return found
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "hithink-finance.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "pnpm" / "hithink-finance.CMD",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("hithink-finance CLI not found")


def run_json(cli: str, args: list[str], output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [cli, *args, "--output", str(output), "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"CLI failed: {' '.join(args)}\n{completed.stderr[-800:]}")
    obj = json.loads(output.read_text(encoding="utf-8"))
    if not obj.get("ok") or obj.get("meta", {}).get("source") != "remote":
        raise RuntimeError(f"Remote response failed: {args}: {obj.get('meta')}")
    return obj


def normalize_item(asset_class: str, code: str, name: str, item: dict, obj: dict, collected_at: str) -> dict:
    if str(item.get("thscode")) == "" or item.get("last_price") is None:
        raise RuntimeError(f"Missing required snapshot fields for {code}")
    return {
        "asset_class": asset_class,
        "code": code,
        "name": name,
        "thscode": item.get("thscode"),
        "last_price": item.get("last_price"),
        "change_pct": item.get("price_change_ratio_pct"),
        "open": item.get("open_price"),
        "high": item.get("high_price"),
        "low": item.get("low_price"),
        "previous_close": item.get("prev_price"),
        "volume": item.get("volume"),
        "amount": item.get("turnover"),
        "turnover_pct": item.get("turnover_ratio_pct"),
        "provider_timestamp_ms": obj.get("data", {}).get("timestamp"),
        "collected_at_asia_shanghai": collected_at,
        "source": "同花顺金融数据CLI远端快照",
    }


def atomic_write_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def atomic_write_csv(path: Path, rows: list[dict]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the V2.2.15 ETF and A-share core-index snapshot.")
    parser.add_argument("--label", default="manual", help="Decision node label, such as 0930 or 1500")
    args = parser.parse_args()

    now = datetime.now(SHANGHAI)
    collected_at = now.isoformat(timespec="seconds")
    run_dir = OUTPUT_ROOT / now.strftime("%Y-%m-%d") / f"{now:%H%M%S}_{args.label}"
    raw_dir = run_dir / "raw"
    cli = prepare_cli()
    rows: list[dict] = []

    for code, name, thscode in ETF:
        obj = run_json(
            cli,
            ["fund", "snapshot", "--thscode", thscode],
            raw_dir / f"ETF_{code}.json",
        )
        items = obj.get("data", {}).get("item") or []
        if len(items) != 1:
            raise RuntimeError(f"Expected one ETF snapshot for {code}, received {len(items)}")
        rows.append(normalize_item("ETF", code, name, items[0], obj, collected_at))

    index_codes = ",".join(item[2] for item in INDEX)
    index_obj = run_json(
        cli,
        ["index", "snapshot", "--thscodes", index_codes],
        raw_dir / "INDEX_core.json",
    )
    returned = {item.get("thscode"): item for item in (index_obj.get("data", {}).get("item") or [])}
    for code, name, thscode in INDEX:
        if thscode not in returned:
            raise RuntimeError(f"Missing index snapshot for {thscode}")
        rows.append(normalize_item("A股指数", code, name, returned[thscode], index_obj, collected_at))

    if len(rows) != 10:
        raise RuntimeError(f"Expected 10 default snapshots, received {len(rows)}")

    manifest = {
        "run_at_asia_shanghai": collected_at,
        "decision_node": args.label,
        "source": "hithink-finance CLI remote",
        "default_scope": "V2.2.15八ETF + 上证指数 + 创业板指",
        "count": len(rows),
        "etf_count": sum(row["asset_class"] == "ETF" for row in rows),
        "a_share_index_count": sum(row["asset_class"] == "A股指数" for row in rows),
        "overseas_boundary": "NDX及条件调用的SOXQ/N225/HSTECH/黄金上游不在当前同花顺主数据范围，须另取已核验来源并标明可获得时点。",
        "account_boundary": "本快照不包含券商账户、持仓、成本、现金、委托或真实成交。",
        "rows": rows,
    }
    atomic_write_json(run_dir / "snapshot.json", manifest)
    atomic_write_csv(run_dir / "snapshot.csv", rows)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUTPUT_ROOT / "latest_snapshot.json", manifest)
    atomic_write_csv(OUTPUT_ROOT / "latest_snapshot.csv", rows)
    print(json.dumps({
        "ok": True,
        "run_dir": str(run_dir),
        "count": len(rows),
        "etf_count": manifest["etf_count"],
        "a_share_index_count": manifest["a_share_index_count"],
        "collected_at": collected_at,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
