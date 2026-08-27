from __future__ import annotations

from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
MIRROR_DIR = ROOT / "maintenance" / "actions_node24_byte_blobs"
MANIFEST = ROOT / "maintenance" / "actions_node24_byte_manifest.json"
TEMP = ".github/workflows/actions-node24-byte-validate-temp.yml"
REPLACEMENTS = {
    b"actions/checkout@v4": b"actions/checkout@v5",
    b"actions/setup-python@v5": b"actions/setup-python@v6",
}

if MIRROR_DIR.exists():
    shutil.rmtree(MIRROR_DIR)
MIRROR_DIR.mkdir(parents=True, exist_ok=True)

changed = []
for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    original = path.read_bytes()
    updated = original
    counts = {}
    for old, new in REPLACEMENTS.items():
        count = updated.count(old)
        if count:
            updated = updated.replace(old, new)
            counts[f"{old.decode()}->{new.decode()}"] = count
    if updated == original:
        continue
    path.write_bytes(updated)
    changed.append({
        "path": rel,
        "original_size": len(original),
        "updated_size": len(updated),
        "size_delta": len(updated) - len(original),
        "replacements": counts,
    })
    if rel != TEMP:
        (MIRROR_DIR / path.name).write_bytes(updated)

production = [x for x in changed if x["path"] != TEMP]
if len(production) != 14:
    raise RuntimeError(f"expected 14 long-lived workflow upgrades, got {len(production)}: {[x['path'] for x in production]}")

for row in production:
    expected_delta = sum(row["replacements"].values()) * 0
    if row["size_delta"] != expected_delta:
        raise RuntimeError(f"unexpected byte-size change: {row}")

remaining = []
for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
    data = path.read_bytes()
    for old in REPLACEMENTS:
        if old in data:
            remaining.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "token": old.decode()})
if remaining:
    raise RuntimeError(f"deprecated refs remain: {remaining}")

MANIFEST.write_text(json.dumps({
    "mode": "BYTE_PRESERVING_NODE24_WORKFLOW_TRANSFORM",
    "source_branch": "maintenance/actions-node24-byte-preserving-20260827",
    "production_workflow_count": len(production),
    "replacement_contract": {k.decode(): v.decode() for k, v in REPLACEMENTS.items()},
    "changed": production,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"production_workflow_count": len(production), "changed": production}, ensure_ascii=False, indent=2))
