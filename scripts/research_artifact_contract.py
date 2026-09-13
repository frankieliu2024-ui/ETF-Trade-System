from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from formal_file_mutation_gateway import mutate_formal_text, replace_managed_block
except ModuleNotFoundError:
    from scripts.formal_file_mutation_gateway import mutate_formal_text, replace_managed_block

EXPERIENCE_FILE = "ETF交易复盘与经验库_2026.md"
ARCHIVE_START = "<!-- AUTO_COMPLETED_RESEARCH_ARCHIVE_START -->"
ARCHIVE_END = "<!-- AUTO_COMPLETED_RESEARCH_ARCHIVE_END -->"


def load_artifact(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def validate_completed_artifact(path: Path) -> list[str]:
    artifact = load_artifact(path)
    if not artifact:
        return ["artifact is empty or invalid JSON"]
    if artifact.get("artifact_kind") != "COMPLETED_RESEARCH_VALIDATION":
        return ["artifact_kind is not a completed research validation"]
    required = {
        "schema_version": artifact.get("schema_version"),
        "research_id": artifact.get("research_id"),
        "completion_status": artifact.get("completion_status"),
        "qualification": artifact.get("qualification"),
        "method": artifact.get("method"),
        "data_audit": artifact.get("data_audit"),
        "current_11": artifact.get("current_11"),
        "headline_results": artifact.get("headline_results"),
        "provenance": artifact.get("provenance"),
    }
    errors = [f"missing {key}" for key, value in required.items() if not value]
    if artifact.get("completion_status") != "COMPLETED":
        errors.append("completion_status is not COMPLETED")
    if artifact.get("research_only") is not True:
        errors.append("research_only must be true")
    method = artifact.get("method") or {}
    for key in ("signal", "execution", "cost_round_trip", "pit"):
        if not method.get(key):
            errors.append(f"method.{key} missing")
    audit = artifact.get("data_audit") or {}
    if not isinstance(audit.get("daily_feature_days"), int) or audit["daily_feature_days"] <= 0:
        errors.append("data_audit.daily_feature_days must be positive")
    formal_count = audit.get("current_formal_count")
    if not isinstance(formal_count, int) or formal_count <= 0:
        errors.append("data_audit.current_formal_count must be positive")
    rows = artifact.get("current_11")
    if not isinstance(rows, list):
        errors.append("current_11 must be a list")
        rows = []
    elif isinstance(formal_count, int) and formal_count > 0 and len(rows) != formal_count:
        errors.append("current_11 count must match data_audit.current_formal_count")
    for row in rows:
        if not isinstance(row, dict) or not row.get("code") or not row.get("observations"):
            errors.append("each current_11 row needs code and observations")
            continue
        if not row.get("sample_start") or not row.get("sample_end"):
            errors.append(f"{row.get('code')}: sample range missing")
        core = ((row.get("strategies") or {}).get("20bp") or {}).get("core_mobile") or {}
        if "net_return" not in core or "net_return" not in (row.get("buy_and_hold") or {}):
            errors.append(f"{row.get('code')}: headline strategy result missing")
    headlines = (artifact.get("headline_results") or {}).get("objects")
    if not isinstance(headlines, dict) or len(headlines) < 4:
        errors.append("headline_results.objects must contain recoverable headline results")
    provenance = artifact.get("provenance") or {}
    for key in ("source_commit", "source_path", "source_artifact_sha256", "input_cutoff_market_date", "recovery_method"):
        if not provenance.get(key):
            errors.append(f"provenance.{key} missing")
    if artifact.get("master_8_1", {}).get("status") != "RESEARCH_ONLY":
        errors.append("MASTER 8.1 boundary must remain RESEARCH_ONLY")
    return errors


def completed_artifacts(root: Path) -> list[Path]:
    output = []
    for path in sorted((root / "research" / "backtests").glob("*.json")):
        artifact = load_artifact(path)
        if artifact.get("artifact_kind") == "COMPLETED_RESEARCH_VALIDATION" and artifact.get("completion_status") == "COMPLETED":
            output.append(path)
    return output


def archive_line(artifact: dict, path: Path) -> str:
    audit = artifact["data_audit"]
    return (
        f"- {artifact['research_id']}｜{artifact['qualification']}｜"
        f"{audit['current_formal_count']}只ETF、{audit['daily_feature_days']}个交易日｜"
        f"RESEARCH_ONLY；详细证据：{path.as_posix()}；"
        "不修改MASTER、不产生交易权限或自动交易。"
    )


def archive_completed_research(root: Path) -> bool:
    entries = []
    for path in completed_artifacts(root):
        if validate_completed_artifact(path):
            continue
        entries.append(archive_line(load_artifact(path), path.relative_to(root)))
    if not entries:
        return False
    return mutate_formal_text(
        root,
        EXPERIENCE_FILE,
        lambda text: replace_managed_block(
            text,
            ARCHIVE_START,
            ARCHIVE_END,
            "\n".join(entries),
            before_heading="## 4. OBS观察",
        ),
    )


def validate_report_against_artifact(report: str, artifact: dict) -> list[str]:
    errors = []
    audit = artifact.get("data_audit") or {}
    if f"{audit.get('daily_feature_days')}个交易日" not in report:
        errors.append("report sample size does not match artifact")
    formal_count = audit.get("current_formal_count")
    if not isinstance(formal_count, int) or formal_count <= 0 or f"正式{formal_count}只" not in report:
        errors.append("report does not declare the artifact formal-object scope")
    if "FAIL / RESEARCH_ONLY" not in report:
        errors.append("report qualification boundary is missing")
    objects = (artifact.get("headline_results") or {}).get("objects") or {}
    for code in artifact.get("report_headline_codes") or objects:
        row = objects.get(code) or {}
        expected = f"{float(row['alpha_pct_points']):.2f}pp"
        if not re.search(rf"\|\s*{re.escape(code)}\s*\|[^\n]*\|\s*{re.escape(expected)}\s*\|", report):
            errors.append(f"report headline for {code} does not match artifact")
    return errors


HISTORICAL_PREFLIGHT_STATUSES = {
    "CAPABILITY_ABSENT",
    "CAPABILITY_PRESENT_RUNTIME_UNAVAILABLE",
    "LEGAL_COVERAGE_INSUFFICIENT",
    "PIT_INVALID",
    "SAMPLE_INSUFFICIENT_AFTER_PREFLIGHT",
    "RECOVERY_AVAILABLE_CONTINUE",
}

_HISTORICAL_PREFLIGHT_REQUIRED = (
    "requested_objects",
    "granularity",
    "target_period",
    "capabilities_checked",
    "entries_attempted_or_consumed",
    "coverage_result",
    "common_window_result",
    "pit_boundary",
    "evidence_id",
)


def validate_historical_stop_result(result: dict) -> list[str]:
    """Enforce the preflight-before-stop boundary for research-only results.

    This validates an already-produced result; it never fetches data or writes
    production state. Non-historical results are outside this contract.
    """
    if not isinstance(result, dict):
        return ["research result must be an object"]
    stop_reason = result.get("stop_reason")
    if stop_reason not in {
        "HISTORICAL_DATA_STOP",
        "HISTORICAL_DATA_INSUFFICIENT",
        "HISTORICAL_SAMPLE_INSUFFICIENT",
        "HISTORICAL_COVERAGE_INSUFFICIENT",
    }:
        return []
    evidence = result.get("historical_data_preflight_evidence")
    if not isinstance(evidence, dict):
        return ["historical stop requires HISTORICAL_DATA_PREFLIGHT_EVIDENCE"]
    errors = []
    for key in _HISTORICAL_PREFLIGHT_REQUIRED:
        value = evidence.get(key)
        if value is None or value == "" or value == []:
            errors.append(f"preflight evidence missing {key}")
    status = evidence.get("status")
    if status not in HISTORICAL_PREFLIGHT_STATUSES:
        errors.append("preflight evidence has an invalid status")
    if status == "RECOVERY_AVAILABLE_CONTINUE":
        errors.append("recovery available cannot be a final historical stop")
    if result.get("stop_reason_evidence_id") != evidence.get("evidence_id"):
        errors.append("stop reason must bind the preflight evidence_id")
    return errors


def validate_research_result(result: dict) -> list[str]:
    """Validate the result-level historical stop gate when applicable."""
    return validate_historical_stop_result(result)
