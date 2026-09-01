from __future__ import annotations

"""Canonical low-level mutation gateway for human-readable formal fact files.

This module only controls *how* already-established facts are written. It does
not derive trading permissions, lifecycle labels, amounts, orders, reviews or
research conclusions. MASTER is intentionally outside the allowed target set.
"""

from pathlib import Path
import re
from typing import Callable

ALLOWED_FORMAL_FACT_FILES = frozenset(
    {
        "ETF当前状态_DASHBOARD.md",
        "ETF市场行情档案_2026.md",
        "ETF交易复盘与经验库_2026.md",
    }
)
FORBIDDEN_RULE_FILE = "ETF规则_MASTER.md"
_UPDATE_LINE = re.compile(r"(^> 更新时点：)\d{4}-\d{2}-\d{2}(?P<tail>.*)$", re.MULTILINE)
_FACT_DATE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")


def _sync_last_fact_update_metadata(text: str) -> str:
    match = _UPDATE_LINE.search(text)
    if not match:
        return text
    dates = _FACT_DATE.findall(text)
    if not dates:
        return text
    latest = max(dates)
    return _UPDATE_LINE.sub(lambda m: f"{m.group(1)}{latest}{m.group('tail')}", text, count=1)


def resolve_formal_fact_path(root: Path, filename: str) -> Path:
    name = str(filename or "").strip()
    if name == FORBIDDEN_RULE_FILE:
        raise PermissionError("MASTER cannot be mutated through the formal fact gateway")
    if name not in ALLOWED_FORMAL_FACT_FILES:
        raise PermissionError(f"unregistered formal fact target: {name}")
    return root.resolve() / name


def replace_managed_block(
    text: str,
    start: str,
    end: str,
    block: str,
    *,
    after_heading: bool = False,
    insert_after_heading: bool | None = None,
    before_heading: str | None = None,
) -> str:
    # process_state_sync_request historically used insert_after_heading while
    # sync_formal_files used after_heading. The gateway accepts both during the
    # migration so behavior is unchanged and callers share one implementation.
    if insert_after_heading is not None:
        after_heading = bool(insert_after_heading)
    managed = f"{start}\n{block.rstrip()}\n{end}"
    if start in text and end in text:
        a = text.index(start)
        b = text.index(end, a) + len(end)
        return text[:a] + managed + text[b:]
    if after_heading:
        lines = text.splitlines()
        pos = 1 if lines and lines[0].startswith("#") else 0
        lines[pos:pos] = ["", managed, ""]
        return "\n".join(lines).rstrip() + "\n"
    if before_heading:
        if before_heading not in text:
            raise ValueError(f"semantic anchor missing: {before_heading}")
        pos = text.index(before_heading)
        return text[:pos].rstrip() + "\n\n" + managed + "\n\n" + text[pos:].lstrip()
    return text.rstrip() + "\n\n" + managed + "\n"


def append_managed_line(text: str, start: str, end: str, line: str) -> str:
    if start in text and end in text:
        a = text.index(start) + len(start)
        b = text.index(end, a)
        existing = text[a:b].strip()
        body = (existing + "\n" + line).strip() if existing else line
        return text[:a] + "\n" + body + "\n" + text[b:]
    return text.rstrip() + f"\n\n{start}\n{line}\n{end}\n"


def upsert_managed_line(text: str, start: str, end: str, key: str, line: str, *, before_heading: str | None = None) -> str:
    tagged = f"{key}｜{line}"
    if start in text and end in text:
        a = text.index(start) + len(start)
        b = text.index(end, a)
        rows = [x for x in text[a:b].strip().splitlines() if x.strip()]
        rows = [x for x in rows if not x.startswith(f"{key}｜")]
        rows.append(tagged)
        return text[:a] + "\n" + "\n".join(rows) + "\n" + text[b:]
    managed = f"{start}\n{tagged}\n{end}"
    if before_heading:
        if before_heading not in text:
            raise ValueError(f"semantic anchor missing: {before_heading}")
        pos = text.index(before_heading)
        return text[:pos].rstrip() + "\n\n" + managed + "\n\n" + text[pos:].lstrip()
    return text.rstrip() + "\n\n" + managed + "\n"


def write_formal_text_if_changed(root: Path, filename: str, new_text: str) -> bool:
    path = resolve_formal_fact_path(root, filename)
    raw = path.read_bytes()
    newline = b"\\r\\n" if b"\\r\\n" in raw else b"\\n"
    prior = raw.decode("utf-8").replace("\\r\\n", "\\n")
    candidate = new_text.replace("\\r\\n", "\\n")
    if prior == candidate:
        return False
    updated = _sync_last_fact_update_metadata(candidate)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(updated.replace("\\n", "\\r\\n" if newline == b"\\r\\n" else "\\n").encode("utf-8"))
    tmp.replace(path)
    return True


def mutate_formal_text(root: Path, filename: str, transform: Callable[[str], str]) -> bool:
    path = resolve_formal_fact_path(root, filename)
    raw = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    prior = raw.decode("utf-8").replace("\r\n", "\n")
    updated = transform(prior)
    if updated == prior:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(updated.replace("\n", "\r\n" if newline == b"\r\n" else "\n").encode("utf-8"))
    tmp.replace(path)
    return True


def replace_formal_block(
    root: Path,
    filename: str,
    start: str,
    end: str,
    block: str,
    *,
    after_heading: bool = False,
) -> bool:
    return mutate_formal_text(
        root,
        filename,
        lambda text: replace_managed_block(text, start, end, block, after_heading=after_heading),
    )


def upsert_formal_line(root: Path, filename: str, start: str, end: str, key: str, line: str, *, before_heading: str | None = None) -> bool:
    return mutate_formal_text(root, filename, lambda text: upsert_managed_line(text, start, end, key, line, before_heading=before_heading))

