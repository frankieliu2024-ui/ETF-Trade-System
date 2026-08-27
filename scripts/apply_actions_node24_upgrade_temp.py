from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
REPLACEMENTS = {
    "actions/checkout@v4": "actions/checkout@v5",
    "actions/setup-python@v5": "actions/setup-python@v6",
}

changed: list[dict] = []
for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
    text = path.read_text(encoding="utf-8")
    updated = text
    counts: dict[str, int] = {}
    for old, new in REPLACEMENTS.items():
        count = updated.count(old)
        if count:
            updated = updated.replace(old, new)
            counts[f"{old}->{new}"] = count
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        changed.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "replacements": counts})

if not changed:
    raise RuntimeError("no deprecated action majors found; refusing no-op maintenance commit")

remaining = []
for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
    text = path.read_text(encoding="utf-8")
    for old in REPLACEMENTS:
        if old in text:
            remaining.append({"path": str(path.relative_to(ROOT)), "token": old})
if remaining:
    raise RuntimeError(f"deprecated action references remain: {remaining}")

print(json.dumps({"changed_count": len(changed), "changed": changed}, ensure_ascii=False, indent=2))
