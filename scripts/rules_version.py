"""Canonical parser for the current MASTER rule release metadata.

Only the release heading, top positioning metadata, and version table are
authoritative for the current version. Historical paragraphs are deliberately
not scanned to infer the current release.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

VERSION_RE = r"V\d+\.\d+\.\d+"
HEADING_RE = re.compile(rf"^# ETF波段交易系统 (?P<version>{VERSION_RE}) 规则 MASTER\s*$")
POSITION_RE = re.compile(rf"^> 定位：(?P<version>{VERSION_RE})(?=[^0-9.]|$)")
DATE_RE = re.compile(r"^> 更新日期：(?P<date>\d{4}-\d{2}-\d{2})")
TABLE_RE = re.compile(rf"^\|(?P<version>{VERSION_RE})\|(?P<position>[^|]+)\|(?P<status>[^|]+)\|\s*$")


def parse_master_release(text: str) -> dict[str, Any]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    errors: list[str] = []
    heading = [m for line in lines if (m := HEADING_RE.match(line))]
    positions = [m for line in lines if (m := POSITION_RE.match(line))]
    dates = [m for line in lines if (m := DATE_RE.match(line))]
    table_rows = [m for line in lines if (m := TABLE_RE.match(line))]
    current_rows = [m for m in table_rows if "当前" in m.group("status")]

    heading_version = heading[0].group("version") if len(heading) == 1 else None
    positioning_version = positions[0].group("version") if len(positions) == 1 else None
    update_date = dates[0].group("date") if len(dates) == 1 else None
    current_table_version = current_rows[0].group("version") if len(current_rows) == 1 else None

    if len(heading) != 1:
        errors.append("MASTER heading must contain exactly one canonical current version")
    if len(positions) != 1:
        errors.append("MASTER positioning metadata must contain exactly one version")
    if len(dates) != 1:
        errors.append("MASTER update date must contain exactly one ISO date")
    if len(current_rows) != 1:
        errors.append("MASTER version table must contain exactly one current-version row")
    if heading_version and positioning_version and heading_version != positioning_version:
        errors.append("MASTER heading and positioning versions differ")
    if heading_version and current_table_version and heading_version != current_table_version:
        errors.append("MASTER heading and current version-table row differ")

    previous_version = None
    current_description_present = False
    if heading_version:
        major, minor, patch = (int(x) for x in heading_version[1:].split("."))
        previous_version = f"V{major}.{minor}.{patch - 1}" if patch > 0 else None
        if previous_version and not any(m.group("version") == previous_version for m in table_rows):
            errors.append(f"MASTER version table is missing previous release {previous_version}")
        # A release description is a prose line beginning with the version,
        # not a table row. This avoids counting historical mentions elsewhere.
        current_description_present = any(
            line.startswith(f"{heading_version}") and not line.startswith("|")
            for line in lines
        )
        if not current_description_present:
            errors.append(f"MASTER is missing a complete description for {heading_version}")

    return {
        "ok": not errors,
        "version": heading_version,
        "heading_version": heading_version,
        "positioning_version": positioning_version,
        "update_date": update_date,
        "current_table_version": current_table_version,
        "table_rows": [m.groupdict() for m in table_rows],
        "previous_version": previous_version,
        "previous_version_present": bool(previous_version and any(m.group("version") == previous_version for m in table_rows)),
        "current_description_present": current_description_present,
        "errors": errors,
    }


def parse_master_release_file(path: Path) -> dict[str, Any]:
    try:
        return parse_master_release(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {"ok": False, "version": None, "errors": [f"cannot read MASTER: {exc}"]}


def current_rule_version(root: Path) -> str | None:
    result = parse_master_release_file(root / "ETF规则_MASTER.md")
    return result["version"] if result.get("ok") else None

