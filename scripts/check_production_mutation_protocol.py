from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/maintenance/production_mutation_protocol.json"
WORKFLOWS = ROOT / ".github/workflows"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_direct_main_writer(text: str) -> bool:
    patterns = (
        r"git\s+push[^\n]*(?:HEAD:main|origin\s+main)",
        r"git\s+push[^\n]*refs/heads/main",
    )
    return any(re.search(p, text) for p in patterns)


def _git_add_mentions(text: str, path: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("git add"):
            continue
        if path in s:
            return True
    return False


def _broad_state_add_without_exclusion(text: str, path: str) -> bool:
    if path != "data/state/system_consistency.json":
        return False
    broad = False
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^git add(?:\s+-A)?(?:\s+--)?\s+data/state(?:\s|$)", s):
            broad = True
            break
        if "git add -A -- data/market/snapshots data/state" in s:
            broad = True
            break
    excluded = any(
        token in text
        for token in (
            "git reset -- data/state/system_consistency.json",
            "git checkout -- data/state/system_consistency.json",
        )
    )
    return broad and not excluded


def run(root: Path = ROOT) -> dict:
    config_path = root / CONFIG.relative_to(ROOT)
    workflows_dir = root / WORKFLOWS.relative_to(ROOT)
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, warning: bool = False) -> None:
        status = "PASS" if ok else ("WARNING" if warning else "FAIL")
        checks.append({"name": name, "status": status, "detail": detail})
        if not ok:
            (warnings if warning else errors).append(f"{name}: {detail}")

    check("mutation_protocol:config_exists", config_path.exists(), str(config_path.relative_to(root)))
    if not config_path.exists():
        return {"status": "FAIL", "errors": errors, "warnings": warnings, "checks": checks, "writers": []}

    cfg = _read_json(config_path)
    requirements = cfg.get("writer_requirements") or {}
    sync_markers = [str(x) for x in requirements.get("latest_main_sync_markers") or []]
    writer_rows = []

    workflow_texts: dict[str, str] = {}
    for path in sorted(workflows_dir.glob("*.yml")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        workflow_texts[rel] = text
        if not _is_direct_main_writer(text):
            continue
        writer_rows.append(rel)
        check(f"mutation_writer:{rel}:concurrency", "concurrency:" in text, "direct main writer has concurrency group")
        check(
            f"mutation_writer:{rel}:latest_main_sync",
            any(marker in text for marker in sync_markers),
            "direct main writer synchronizes latest main before production push",
        )
        check(
            f"mutation_writer:{rel}:no_force_push",
            not re.search(r"git\s+push[^\n]*--force(?:-with-lease)?[^\n]*main", text),
            "direct main writer does not force-push main",
        )

    check("mutation_protocol:direct_writers_discovered", bool(writer_rows), f"writers={writer_rows}")

    single_owner = cfg.get("single_owner_files") or {}
    for owned_path, owner in single_owner.items():
        for rel, text in workflow_texts.items():
            if owner == "NO_AUTOMATIC_WORKFLOW":
                violation = _git_add_mentions(text, owned_path)
                check(
                    f"mutation_owner:{owned_path}:{rel}",
                    not violation,
                    "formal single-owner file is not automatically staged",
                )
                continue
            if rel == owner:
                continue
            violation = _git_add_mentions(text, owned_path) or _broad_state_add_without_exclusion(text, owned_path)
            check(
                f"mutation_owner:{owned_path}:{rel}",
                not violation,
                f"single-owner file remains owned by {owner}",
            )

    for family in cfg.get("shared_writer_families") or []:
        token = str(family.get("required_concurrency_token") or "")
        family_id = str(family.get("family_id") or "UNKNOWN")
        for member in family.get("members") or []:
            text = workflow_texts.get(str(member), "")
            check(
                f"mutation_family:{family_id}:{member}",
                bool(text) and token in text,
                f"shared writer family uses concurrency token {token}",
            )

    return {
        "schema_version": "1.0",
        "mode": "PRODUCTION_MUTATION_PROTOCOL_CHECK",
        "status": "FAIL" if errors else ("WARNING" if warnings else "PASS"),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "direct_main_writers": writer_rows,
        "fact_precedence": cfg.get("fact_precedence") or [],
    }


def main() -> int:
    result = run(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
