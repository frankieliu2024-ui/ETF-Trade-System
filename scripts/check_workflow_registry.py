#!/usr/bin/env python3
"""Validate stable GitHub Actions workflow names and cross-workflow references."""

from __future__ import annotations

import re
import sys
from pathlib import Path


WORKFLOW_ROOT = Path(".github/workflows")
NAME_RE = re.compile(r"^name:\s*(?P<value>.*)\s*$")
WORKFLOW_RUN_RE = re.compile(r'^\s*-\s*["\'](?P<value>.+?)["\']\s*$')


def scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
        return value[1:-1]
    return value


def read_workflow_name(path: Path) -> str:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = NAME_RE.match(line)
        if match:
            name = scalar(match.group("value"))
            if not name:
                raise SystemExit(f"{path}: workflow name is empty")
            if name == path.name or name == path.stem:
                raise SystemExit(f"{path}: workflow name falls back to file path")
            return name
        # name must be a top-level declaration before the event/job sections.
        if line.startswith(("on:", "permissions:", "concurrency:", "env:", "jobs:")):
            break
    raise SystemExit(f"{path}: missing top-level workflow name")


def referenced_workflow_names(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    refs: list[str] = []
    in_workflows = False
    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^\s*workflows:\s*$", line):
            in_workflows = True
            continue
        if in_workflows:
            if line and not line.startswith((" ", "\t")):
                in_workflows = False
                continue
            match = WORKFLOW_RUN_RE.match(line)
            if match:
                refs.append(match.group("value"))
    return refs


def main() -> int:
    files = sorted(WORKFLOW_ROOT.glob("*.yml")) + sorted(WORKFLOW_ROOT.glob("*.yaml"))
    if not files:
        raise SystemExit("no workflow files found")

    names = {path: read_workflow_name(path) for path in files}
    reverse: dict[str, list[Path]] = {}
    for path, name in names.items():
        reverse.setdefault(name, []).append(path)
    duplicates = {name: paths for name, paths in reverse.items() if len(paths) > 1}
    if duplicates:
        details = "; ".join(f"{name}: {', '.join(str(p) for p in paths)}" for name, paths in duplicates.items())
        raise SystemExit(f"duplicate workflow names: {details}")

    known = set(names.values())
    errors: list[str] = []
    for path in files:
        for ref in referenced_workflow_names(path):
            if ref not in known:
                errors.append(f"{path}: workflow_run references unknown workflow {ref!r}")
    if errors:
        raise SystemExit("\n".join(errors))

    print(f"workflow registry contract: PASS ({len(files)} workflows, {len(known)} unique names)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
