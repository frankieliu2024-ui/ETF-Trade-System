"""Classify main movement without treating runtime state churn as source drift."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


STABLE_PREFIXES = ("scripts/", ".github/workflows/", "config/", "tests/", "docs/")
FORMAL_FILES = {
    "ETF规则_MASTER.md",
    "ETF_SYSTEM_INDEX.md",
    "ETF当前状态_DASHBOARD.md",
    "ETF交易复盘与经验库_2026.md",
    "ETF市场行情档案_2026.md",
    "ETF与市场监测数据接口使用规范.md",
}
REQUEST_PREFIXES = ("requests/",)
DYNAMIC_PREFIXES = ("data/state/", "data/market/snapshots/", "data/market/audit/")


def classify_path(path: str) -> str:
    path = path.replace("\\", "/")
    if path in FORMAL_FILES or path.startswith("events/"):
        return "FORMAL_FACT_MUTATION"
    if path.startswith(REQUEST_PREFIXES):
        return "REQUEST_OR_TRIGGER_FACT"
    if path.startswith(DYNAMIC_PREFIXES):
        return "DYNAMIC_RUNTIME_FACT"
    if path.startswith(STABLE_PREFIXES):
        return "STABLE_PRODUCTION_CHANGE"
    return "UNKNOWN"


def _changed_paths(root: Path, base: str, head: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git diff failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def classify_delta(root: Path, base: str, head: str) -> dict:
    paths = _changed_paths(root, base, head)
    categories = {category: [] for category in (
        "STABLE_PRODUCTION_CHANGE", "DYNAMIC_RUNTIME_FACT", "FORMAL_FACT_MUTATION",
        "REQUEST_OR_TRIGGER_FACT", "UNKNOWN",
    )}
    for path in paths:
        categories[classify_path(path)].append(path)
    present = {key for key, values in categories.items() if values}
    if not present:
        decision = "SEMANTICALLY_FRESH"
    elif present <= {"DYNAMIC_RUNTIME_FACT"}:
        decision = "SEMANTICALLY_FRESH"
    elif "UNKNOWN" in present or "STABLE_PRODUCTION_CHANGE" in present:
        decision = "REPLAY_REQUIRED"
    else:
        decision = "REVIEW_REQUIRED"
    return {
        "base": base,
        "head": head,
        "changed_paths": paths,
        "categories": categories,
        "decision": decision,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    result = classify_delta(Path(args.root), args.base, args.head)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["decision"] == "REPLAY_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
