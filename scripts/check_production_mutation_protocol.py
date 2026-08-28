from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/maintenance/production_mutation_protocol.json"
WORKFLOWS = ROOT / ".github/workflows"
SCRIPTS = ROOT / "scripts"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_direct_main_writer(text: str) -> bool:
    # Require an exact main ref boundary. Without the boundary, HEAD:maintenance/*
    # is falsely matched by the HEAD:main prefix.
    patterns = (
        r"git\s+push[^\n]*(?:HEAD:main(?=\s|$)|origin\s+main(?=\s|$))",
        r"git\s+push[^\n]*refs/heads/main(?=\s|$)",
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
    if not path.startswith("data/state/"):
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
            f"git reset -- {path}",
            f"git checkout -- {path}",
            f"git restore --staged {path}",
        )
    )
    return broad and not excluded


def _workflow_may_stage(text: str, path: str) -> bool:
    return _git_add_mentions(text, path) or _broad_state_add_without_exclusion(text, path)


def _bounded_current_repair_is_narrow(root: Path, script_path: str) -> bool:
    path = root / script_path
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    writes = re.findall(r"current\s*\[\s*[\"']([^\"']+)[\"']\s*\]\s*=", text)
    return bool(writes) and set(writes) == {"rules_version"}


def run(root: Path = ROOT) -> dict:
    config_path = root / CONFIG.relative_to(ROOT)
    workflows_dir = root / WORKFLOWS.relative_to(ROOT)
    scripts_dir = root / SCRIPTS.relative_to(ROOT)
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
            not re.search(r"git\s+push[^\n]*--force(?:-with-lease)?[^\n]*(?:HEAD:main(?=\s|$)|origin\s+main(?=\s|$)|refs/heads/main(?=\s|$))", text),
            "direct main writer does not force-push main",
        )

    # Production reliability is a separate runtime responsibility, but the
    # consistency gate must ensure that its control-plane wiring still exists.
    # These checks are static only: they do not call providers, query Actions,
    # or execute watchdog recovery paths.
    self_heal_workflow_rel = ".github/workflows/self-healing-watchdog.yml"
    failure_guard_workflow_rel = ".github/workflows/workflow-failure-guard.yml"
    self_heal_script_rel = "scripts/runtime_self_heal.py"
    failure_guard_script_rel = "scripts/workflow_failure_guard.py"
    self_heal_workflow = workflow_texts.get(self_heal_workflow_rel, "")
    failure_guard_workflow = workflow_texts.get(failure_guard_workflow_rel, "")
    self_heal_script_path = root / self_heal_script_rel
    failure_guard_script_path = root / failure_guard_script_rel
    self_heal_script = self_heal_script_path.read_text(encoding="utf-8") if self_heal_script_path.exists() else ""
    failure_guard_script = failure_guard_script_path.read_text(encoding="utf-8") if failure_guard_script_path.exists() else ""
    protected_workflows = (
        "ETF system consistency",
        "ETF runtime self-healing watchdog",
        "Overseas pre-open pulse",
        "US extended-hours pulse",
    )

    check(
        "reliability:self_healing_wired",
        bool(self_heal_workflow and self_heal_script and "python scripts/runtime_self_heal.py --assess" in self_heal_workflow),
        "self-healing watchdog exists and assesses runtime through the canonical runtime_self_heal.py",
    )
    check(
        "reliability:canonical_snapshot_recovery",
        bool(self_heal_workflow and "gh workflow run market-snapshot.yml --ref main" in self_heal_workflow),
        "stale A-share recovery redispatches the canonical market-snapshot workflow",
    )
    check(
        "reliability:cross_market_heartbeat_wired",
        bool(
            self_heal_workflow
            and "gh workflow run overseas-preopen-pulse.yml --ref main" in self_heal_workflow
            and "gh workflow run us-extended-hours-pulse.yml --ref main" in self_heal_workflow
        ),
        "stale APAC/US heartbeats redispatch their canonical production pulse workflows",
    )
    check(
        "reliability:failure_guard_wired",
        bool(
            failure_guard_workflow
            and failure_guard_script
            and "python scripts/workflow_failure_guard.py" in failure_guard_workflow
            and all(name in failure_guard_workflow for name in protected_workflows)
            and all(name in failure_guard_script for name in protected_workflows)
        ),
        "workflow failure guard exists, invokes the canonical classifier, and covers all four core reliability workflows",
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
            violation = _workflow_may_stage(text, owned_path)
            check(
                f"mutation_owner:{owned_path}:{rel}",
                not violation,
                f"single-owner file remains owned by {owner}",
            )

    formal = cfg.get("formal_file_mutation_contract") or {}
    gateway_rel = str(formal.get("canonical_gateway") or "")
    gateway_path = root / gateway_rel if gateway_rel else Path()
    allowed_fact_files = [str(x) for x in formal.get("allowed_fact_files") or []]
    forbidden_rule = str(formal.get("forbidden_rule_file") or "")
    registered_callers = [str(x) for x in formal.get("registered_callers") or []]
    gateway_text = gateway_path.read_text(encoding="utf-8") if gateway_rel and gateway_path.exists() else ""
    check("formal_gateway:exists", bool(gateway_rel and gateway_path.exists()), gateway_rel or "missing canonical gateway")
    check(
        "formal_gateway:allowed_fact_files",
        bool(allowed_fact_files) and all(name in gateway_text for name in allowed_fact_files),
        f"allowed={allowed_fact_files}",
    )
    check(
        "formal_gateway:master_forbidden",
        bool(forbidden_rule and forbidden_rule in gateway_text and "raise PermissionError" in gateway_text),
        f"forbidden={forbidden_rule}",
    )
    for caller in registered_callers:
        path = root / caller
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        check(f"formal_gateway:caller:{caller}:exists", path.exists(), "registered formal mutation caller exists")
        check(
            f"formal_gateway:caller:{caller}:wired",
            bool(text) and "formal_file_mutation_gateway" in text,
            "registered caller uses canonical formal mutation gateway",
        )
    bypass_patterns = (
        r"\bDASHBOARD\.write_text\(",
        r"\bARCHIVE\.write_text\(",
        r"\bEXPERIENCE\.write_text\(",
        r"\bdash_path\.write_text\(",
        r"\barchive_path\.write_text\(",
        r"\bexperience_path\.write_text\(",
    )
    bypasses: list[str] = []
    for path in sorted(scripts_dir.glob("*.py")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        if rel == gateway_rel:
            continue
        text = path.read_text(encoding="utf-8")
        if any(re.search(pattern, text) for pattern in bypass_patterns):
            bypasses.append(rel)
    check(
        "formal_gateway:no_direct_formal_write_bypass",
        not bypasses,
        f"direct_write_bypasses={bypasses}",
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

    for rel, contract in (cfg.get("nonproduction_validation_workflows") or {}).items():
        text = workflow_texts.get(str(rel), "")
        artifact_path = str((contract or {}).get("artifact_path") or "")
        check(f"nonproduction_validation:{rel}:exists", bool(text), "registered nonproduction validation workflow exists")
        check(
            f"nonproduction_validation:{rel}:no_main_write",
            bool(text) and not _is_direct_main_writer(text),
            "PoC/shadow workflow cannot directly write main",
        )
        check(
            f"nonproduction_validation:{rel}:read_only_contents",
            bool(text) and "contents: read" in text and "contents: write" not in text,
            "PoC/shadow workflow has read-only repository contents permission",
        )
        check(
            f"nonproduction_validation:{rel}:artifact",
            bool(text) and "actions/upload-artifact@v4" in text and artifact_path and artifact_path in text,
            f"validation evidence is uploaded as artifact path={artifact_path}",
        )

    for state_path, contract in (cfg.get("state_file_contracts") or {}).items():
        state_class = str(contract.get("state_class") or "UNKNOWN")
        canonical_builder = str(contract.get("canonical_builder") or "")
        canonical_writers = [str(x) for x in contract.get("canonical_writers") or []]
        allowed_writers = [str(x) for x in contract.get("allowed_writers") or []]
        repair_writers = contract.get("bounded_repair_writers") or {}
        permitted = set(canonical_writers) | set(allowed_writers) | set(repair_writers)

        check(
            f"state_contract:{state_path}:registered",
            bool(state_class and canonical_builder),
            f"class={state_class} builder={canonical_builder}",
        )
        for rel, text in workflow_texts.items():
            stages = _workflow_may_stage(text, state_path)
            if stages and rel not in permitted:
                check(f"state_contract:{state_path}:writer:{rel}", False, f"unregistered production writer for {state_path}")
        for rel in canonical_writers + allowed_writers:
            text = workflow_texts.get(rel, "")
            check(
                f"state_contract:{state_path}:builder:{rel}",
                bool(text) and canonical_builder in text,
                f"registered writer invokes canonical builder {canonical_builder}",
            )
            check(
                f"state_contract:{state_path}:staging:{rel}",
                bool(text) and _workflow_may_stage(text, state_path),
                f"registered writer persists {state_path}",
            )
        for rel, repair in repair_writers.items():
            text = workflow_texts.get(str(rel), "")
            required_script = str((repair or {}).get("required_script") or "")
            check(
                f"state_contract:{state_path}:bounded_repair:{rel}:wired",
                bool(text) and required_script and required_script in text and _workflow_may_stage(text, state_path),
                f"bounded repair writer uses {required_script} and persists {state_path}",
            )
            if state_path == "data/state/CURRENT.json":
                check(
                    f"state_contract:{state_path}:bounded_repair:{rel}:narrow",
                    _bounded_current_repair_is_narrow(root, required_script),
                    "bounded CURRENT repair mutates rules_version only",
                )

    return {
        "schema_version": "1.3",
        "mode": "PRODUCTION_MUTATION_PROTOCOL_CHECK",
        "status": "FAIL" if errors else ("WARNING" if warnings else "PASS"),
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "direct_main_writers": writer_rows,
        "formal_file_mutation_contract": formal,
        "nonproduction_validation_workflows": cfg.get("nonproduction_validation_workflows") or {},
        "state_file_contracts": cfg.get("state_file_contracts") or {},
        "fact_precedence": cfg.get("fact_precedence") or [],
    }


def main() -> int:
    result = run(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())