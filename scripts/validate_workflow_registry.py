#!/usr/bin/env python3
"""Validate GitHub workflow registry structure before merge.

This intentionally avoids a new runtime dependency.  GitHub remains the final
YAML parser, while this guard catches the failure class that previously reached
main: malformed shell heredocs embedded in YAML block scalars.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
HEREDOC_START = re.compile(r'''<<-?(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1''')


def leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def validate_workflow(path: Path) -> list[str]:
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return [f"{path}: empty workflow"]
    if not any(line.startswith("name:") for line in lines[:10]):
        errors.append(f"{path}: missing top-level name")
    if not any(re.match(r"^on\s*:", line) for line in lines[:30]):
        errors.append(f"{path}: missing top-level on")
    if not any(re.match(r"^jobs\s*:", line) for line in lines):
        errors.append(f"{path}: missing top-level jobs")

    active: tuple[str, int, int, int] | None = None
    for number, line in enumerate(lines, 1):
        match = HEREDOC_START.search(line) if active is None else None
        if match:
            delimiter = match.group(2)
            run_indent = None
            for previous in reversed(lines[: number - 1]):
                if re.match(r"^\\s*run\\s*:\\s*\\|", previous):
                    run_indent = leading_spaces(previous)
                    break
            expected_indent = (run_indent + 2) if run_indent is not None else leading_spaces(line)
            active = (delimiter, expected_indent, number, leading_spaces(line))
            continue
        if active is None:
            continue
        delimiter, expected_indent, start_line, _command_indent = active
        if line.strip() == delimiter:
            if leading_spaces(line) < expected_indent:
                errors.append(
                    f"{path}:{number}: heredoc terminator {delimiter!r} is less indented "
                    f"than its YAML run block (heredoc line {start_line})"
                )
            active = None
            continue
        if line.strip() and leading_spaces(line) < expected_indent:
            errors.append(
                f"{path}:{number}: heredoc body is less indented than its command "
                f"(line {start_line})"
            )
    if active is not None:
        delimiter, _, start_line = active
        errors.append(f"{path}:{start_line}: unterminated heredoc {delimiter!r}")
    return errors


def main() -> int:
    paths = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    if not paths:
        print("no GitHub workflow files found", file=sys.stderr)
        return 1
    errors = [error for path in paths for error in validate_workflow(path)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"workflow registry guard passed: {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
