from __future__ import annotations

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
MANIFEST = ROOT / "maintenance" / "actions_node24_workflows.json"
TEMP_WORKFLOW = ".github/workflows/actions-node24-upgrade-temp.yml"
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

manifest_files: dict[str, str] = {}
for row in changed:
    rel = row["path"]
    if rel == TEMP_WORKFLOW:
        continue
    manifest_files[rel] = (ROOT / rel).read_text(encoding="utf-8")

if len(manifest_files) != 14:
    raise RuntimeError(f"expected 14 production workflow upgrades, got {len(manifest_files)}: {sorted(manifest_files)}")

MANIFEST.parent.mkdir(parents=True, exist_ok=True)
MANIFEST.write_text(
    json.dumps(
        {
            "mode": "VALIDATED_NODE24_WORKFLOW_MANIFEST",
            "source_branch": "maintenance/actions-node24-runtime-20260827",
            "replacement_contract": REPLACEMENTS,
            "production_workflow_count": len(manifest_files),
            "files": manifest_files,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

print(json.dumps({"changed_count": len(changed), "production_manifest_count": len(manifest_files), "changed": changed}, ensure_ascii=False, indent=2))
