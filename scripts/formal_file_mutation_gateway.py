from __future__ import annotations

"""Canonical low-level mutation gateway for human-readable formal fact files.

This module only controls *how* already-established facts are written. It does
not derive trading permissions, lifecycle labels, amounts, orders, reviews or
research conclusions. MASTER is intentionally outside the allowed target set.
"""

import json
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
DASHBOARD_FILE = "ETF当前状态_DASHBOARD.md"
EXPERIENCE_FILE = "ETF交易复盘与经验库_2026.md"
_UPDATE_LINE = re.compile(r"(^> 更新时点：)\d{4}-\d{2}-\d{2}(?P<tail>.*)$", re.MULTILINE)
_FACT_DATE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_COMPACT_CASE_HEADING = re.compile(r"^(### 2\.\d+ CASE-\d{8}-\d{2}[:：][^｜\n]+)｜(.+)$")
_DASHBOARD_DISPLAY_MAP = {
    "ACCOUNT_FACT_MAINTENANCE": "账户事实维护",
    "NO_NEW_FORMAL_MORNING_DECISION_REGISTERED": "本节点未登记新的正式决策",
    "NONE_REGISTERED": "无新的主候选",
}
_DASHBOARD_CORRECTION_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
_DASHBOARD_CORRECTION_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"
_CASE_INTAKE_START = "<!-- AUTO_CASE_INTAKE_START -->"
_CASE_INTAKE_END = "<!-- AUTO_CASE_INTAKE_END -->"


def _sync_last_fact_update_metadata(text: str) -> str:
    match = _UPDATE_LINE.search(text)
    if not match:
        return text
    dates = _FACT_DATE.findall(text)
    if not dates:
        return text
    latest = max(dates)
    return _UPDATE_LINE.sub(lambda m: f"{m.group(1)}{latest}{m.group('tail')}", text, count=1)


def _remove_managed_block(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return text
    a = text.index(start)
    b = text.index(end, a) + len(end)
    prefix = text[:a].rstrip()
    suffix = text[b:].lstrip("\n")
    if not prefix:
        return suffix
    if not suffix:
        return prefix + "\n"
    return prefix + "\n\n" + suffix


def _empty_managed_block(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return text
    a = text.index(start) + len(start)
    b = text.index(end, a)
    return text[:a] + "\n" + text[b:]


def _position_number(position: dict, *keys: str) -> float | None:
    for key in keys:
        if key not in position or position.get(key) is None:
            continue
        try:
            return float(position[key])
        except (TypeError, ValueError):
            continue
    return None


def _account_positions(root: Path) -> dict[str, dict]:
    path = root / "data" / "state" / "account_fact.json"
    try:
        account = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(position.get("code")): position
        for position in (account.get("positions") or [])
        if isinstance(position, dict) and position.get("code")
    }


def _position_pnl_display(position: dict) -> str:
    pnl = _position_number(position, "pnl", "holding_pnl")
    pnl_pct = _position_number(position, "pnl_pct", "holding_pnl_pct")
    if pnl is None and pnl_pct is None:
        return "—"
    amount = "—" if pnl is None else f"{pnl:,.2f}元"
    pct = "—" if pnl_pct is None else f"{pnl_pct:+.2f}%"
    return f"{amount}（{pct}）"


def _normalize_dashboard(root: Path, text: str) -> str:
    # Dashboard is a current human projection. Historical correction routing
    # rows belong in canonical machine facts / archive, not in the current view.
    text = _remove_managed_block(text, _DASHBOARD_CORRECTION_START, _DASHBOARD_CORRECTION_END)
    for machine_value, display_value in _DASHBOARD_DISPLAY_MAP.items():
        text = text.replace(machine_value, display_value)

    positions = _account_positions(root)
    lines = text.splitlines()
    normalized: list[str] = []
    in_lifecycle = False
    for line in lines:
        if line.strip() == "- 生命周期：":
            in_lifecycle = True
            normalized.append(line)
            continue
        if in_lifecycle and line.startswith("- 唯一主候选："):
            in_lifecycle = False
        if in_lifecycle and line.startswith("- "):
            line = "  " + line

        if positions and line.startswith("|"):
            parts = line.split("|")
            if len(parts) >= 8:
                match = re.search(r"（(\d{6})）", parts[1])
                if match and match.group(1) in positions:
                    parts[6] = _position_pnl_display(positions[match.group(1)])
                    line = "|".join(parts)
        normalized.append(line)
    result = "\n".join(normalized)
    return result + ("\n" if text.endswith("\n") else "")


def _normalize_experience(text: str) -> str:
    # AUTO_CASE_INTAKE is routing metadata only. Canonical trade/review events
    # remain machine facts; the human experience file shows completed CASE prose.
    text = _empty_managed_block(text, _CASE_INTAKE_START, _CASE_INTAKE_END)
    lines: list[str] = []
    for line in text.splitlines():
        match = _COMPACT_CASE_HEADING.match(line)
        if not match:
            lines.append(line)
            continue
        summary_parts = [part.strip() for part in match.group(2).split("｜") if part.strip()]
        lines.append(match.group(1).rstrip())
        if summary_parts:
            lines.append(f"- 摘要：{'；'.join(summary_parts)}")
    result = "\n".join(lines)
    return result + ("\n" if text.endswith("\n") else "")


def normalize_human_readable_projection(root: Path, filename: str, text: str) -> str:
    """Normalize presentation-only drift without inventing or changing facts."""
    if filename == DASHBOARD_FILE:
        return _normalize_dashboard(root, text)
    if filename == EXPERIENCE_FILE:
        return _normalize_experience(text)
    return text


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
    """Upsert a dated fact line, or a canonical Markdown heading entry.

    CASE entries are formal projection headings, not dated key/value lines.
    Preserve the heading at column zero so formal validation can recognize the
    projection without making the formal file a second machine fact owner.
    """
    heading_entry = line.lstrip().startswith("### ")
    tagged = line if heading_entry else f"{key}｜{line}"
    if start in text and end in text:
        a = text.index(start) + len(start)
        b = text.index(end, a)
        rows = [x for x in text[a:b].strip().splitlines() if x.strip()]
        if heading_entry:
            marker = line.lstrip().split("：", 1)[0].split(":", 1)[0]
            rows = [x for x in rows if marker not in x]
        else:
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
    candidate = normalize_human_readable_projection(
        root,
        filename,
        new_text.replace("\\r\\n", "\\n"),
    )
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
    updated = normalize_human_readable_projection(root, filename, transform(prior))
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
