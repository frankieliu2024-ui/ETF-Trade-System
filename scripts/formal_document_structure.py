from __future__ import annotations

"""Semantic structure normalizer/validator for human-readable formal fact files.

This module does not create trading facts, CASE conclusions, permissions, or rules.
It only preserves already-established content while keeping that content in the
canonical chapter/section where readers and downstream checks expect it.
"""

import re
from pathlib import Path

EXPERIENCE = "ETF交易复盘与经验库_2026.md"
ARCHIVE = "ETF市场行情档案_2026.md"
DASHBOARD = "ETF当前状态_DASHBOARD.md"

CASE_START = "<!-- AUTO_CASE_INTAKE_START -->"
CASE_END = "<!-- AUTO_CASE_INTAKE_END -->"
REVIEW_CASE_START = "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->"
REVIEW_CASE_END = "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->"
TRADE_START = "<!-- AUTO_TRADE_EVENTS_START -->"
TRADE_END = "<!-- AUTO_TRADE_EVENTS_END -->"
ACCOUNT_START = "<!-- AUTO_ACCOUNT_FACT_SYNC_START -->"
ACCOUNT_END = "<!-- AUTO_ACCOUNT_FACT_SYNC_END -->"
REVIEW_FACT_START = "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->"
REVIEW_FACT_END = "<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->"
CORR_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
CORR_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"


def _extract_block(text: str, start: str, end: str) -> tuple[str, str]:
    if start not in text or end not in text:
        return text, ""
    a = text.index(start)
    b = text.index(end, a) + len(end)
    block = text[a:b].strip()
    left = (text[:a].rstrip() + "\n\n" + text[b:].lstrip()).strip() + "\n"
    return left, block


def _insert_before(text: str, heading: str, blocks: list[str]) -> str:
    blocks = [b.strip() for b in blocks if b and b.strip()]
    if not blocks:
        return text
    if heading not in text:
        raise ValueError(f"semantic anchor missing: {heading}")
    pos = text.index(heading)
    before = text[:pos].rstrip()
    after = text[pos:].lstrip()
    return before + "\n\n" + "\n\n".join(blocks) + "\n\n" + after


def _move_tail_research_into_chapter3(text: str) -> str:
    # Historical research was previously appended after Chapter 6. Move only
    # clearly research-labelled dated sections; preserve maintenance content.
    if "## 6. 版本维护记录" not in text or "## 3. 历史研究与专项回测" not in text or "## 4. OBS观察" not in text:
        return text
    ch6 = text.index("## 6. 版本维护记录")
    tail = text[ch6:]
    pattern = re.compile(
        r"(?ms)^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)\n.*?(?=^### |^## |^<!-- AUTO_|\Z)"
    )
    matches = list(pattern.finditer(tail))
    if not matches:
        return text
    moved = [m.group(0).strip() for m in matches]
    for m in reversed(matches):
        a = ch6 + m.start()
        b = ch6 + m.end()
        text = text[:a] + text[b:]
    insert = text.index("## 4. OBS观察")
    text = text[:insert].rstrip() + "\n\n" + "\n\n".join(moved) + "\n\n" + text[insert:].lstrip()
    return text


def normalize_experience(text: str) -> str:
    # Managed CASE/review blocks belong to Chapter 2, regardless of where an
    # older append-only writer originally placed them.
    text, case_block = _extract_block(text, CASE_START, CASE_END)
    text, review_block = _extract_block(text, REVIEW_CASE_START, REVIEW_CASE_END)
    text = _move_tail_research_into_chapter3(text)

    if "## 2. 真实交易CASE" not in text or "## 3. 历史研究与专项回测" not in text:
        raise ValueError("experience Chapter 2/3 anchor missing")
    i2 = text.index("## 2. 真实交易CASE")
    i3 = text.index("## 3. 历史研究与专项回测", i2)
    ch2 = text[i2:i3]

    # Extract all CASE sections and the contribution index. CASE order is
    # canonical by CASE id (date + same-day sequence), not append time.
    case_pat = re.compile(r"(?ms)^### 2\.\d+ (CASE-\d{8}-\d{2})[:：].*?(?=^### 2\.\d+ |\Z)")
    cases = [(m.group(1), m.group(0).strip()) for m in case_pat.finditer(ch2)]
    for _, block in reversed(cases):
        pos = ch2.rfind(block)
        ch2 = ch2[:pos] + ch2[pos + len(block):]

    contrib_pat = re.compile(r"(?ms)^### 2\.\d+ CASE系统贡献索引.*\Z")
    cm = contrib_pat.search(ch2)
    contribution = cm.group(0).strip() if cm else ""
    if cm:
        ch2 = ch2[:cm.start()].rstrip() + "\n"

    # Preserve 2.1 transaction index and 2.2 non-trading cash flow exactly.
    base = ch2.rstrip()
    rendered = []
    for idx, (case_id, block) in enumerate(sorted(cases, key=lambda x: x[0]), start=3):
        block = re.sub(r"^### 2\.\d+ ", f"### 2.{idx} ", block, count=1)
        rendered.append(block)
    next_idx = 3 + len(rendered)
    if contribution:
        contribution = re.sub(r"^### 2\.\d+ ", f"### 2.{next_idx} ", contribution, count=1)
        rendered.append(contribution)

    chapter2 = base
    if rendered:
        chapter2 += "\n\n" + "\n\n".join(rendered)
    managed = [b for b in (case_block, review_block) if b]
    if managed:
        chapter2 += "\n\n" + "\n\n".join(managed)
    chapter2 = chapter2.rstrip() + "\n\n"
    text = text[:i2] + chapter2 + text[i3:]
    return text.rstrip() + "\n"


def normalize_archive(text: str) -> str:
    # Objective trade/account blocks belong to Chapter 5 historical facts, not
    # Chapter 7 maintenance rules. Trade-fact corrections remain maintenance.
    text, trade_block = _extract_block(text, TRADE_START, TRADE_END)
    text, account_block = _extract_block(text, ACCOUNT_START, ACCOUNT_END)
    if "## 6. 历史Excel与专项数据来源" not in text:
        raise ValueError("archive Chapter 6 anchor missing")
    return _insert_before(text, "## 6. 历史Excel与专项数据来源", [trade_block, account_block]).rstrip() + "\n"


def normalize_text(filename: str, text: str) -> str:
    if filename == EXPERIENCE:
        return normalize_experience(text)
    if filename == ARCHIVE:
        return normalize_archive(text)
    return text


def normalize_files(root: Path) -> dict:
    changed = []
    for filename in (EXPERIENCE, ARCHIVE):
        path = root / filename
        prior = path.read_text(encoding="utf-8")
        updated = normalize_text(filename, prior)
        if updated != prior:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(updated, encoding="utf-8")
            tmp.replace(path)
            changed.append(filename)
    return {"changed": changed}


def _between(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return ""
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def validate_files(root: Path) -> list[str]:
    errors: list[str] = []
    exp = (root / EXPERIENCE).read_text(encoding="utf-8")
    arc = (root / ARCHIVE).read_text(encoding="utf-8")
    dash = (root / DASHBOARD).read_text(encoding="utf-8")

    # Experience top-level chapter order and uniqueness.
    exp_top = re.findall(r"^## ([0-6])\. ", exp, re.MULTILINE)
    if exp_top != list("0123456"):
        errors.append(f"experience_top_level_order={exp_top}")

    ch2 = _between(exp, "## 2. 真实交易CASE", "## 3. 历史研究与专项回测")
    nums = re.findall(r"^### (2\.\d+)\b", ch2, re.MULTILINE)
    if len(nums) != len(set(nums)):
        errors.append("experience_duplicate_chapter2_numbers")
    case_rows = re.findall(r"^### (2\.\d+) (CASE-(\d{8})-(\d{2}))[:：]", ch2, re.MULTILINE)
    case_ids = [x[1] for x in case_rows]
    if case_ids != sorted(case_ids):
        errors.append(f"experience_case_order={case_ids}")
    expected_sections = [f"2.{i}" for i in range(3, 3 + len(case_rows))]
    actual_sections = [x[0] for x in case_rows]
    if actual_sections != expected_sections:
        errors.append(f"experience_case_section_numbers={actual_sections}")
    contrib = re.search(r"^### (2\.\d+) CASE系统贡献索引", ch2, re.MULTILINE)
    expected_contrib = f"2.{3 + len(case_rows)}"
    if not contrib or contrib.group(1) != expected_contrib:
        errors.append(f"experience_contribution_index={contrib.group(1) if contrib else None}")
    for start, end, name in (
        (CASE_START, CASE_END, "case_intake"),
        (REVIEW_CASE_START, REVIEW_CASE_END, "post_close_cases"),
    ):
        if start in exp and not (start in ch2 and end in ch2):
            errors.append(f"experience_managed_block_outside_ch2={name}")

    ch3 = _between(exp, "## 3. 历史研究与专项回测", "## 4. OBS观察")
    all_research = re.findall(r"^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)", exp, re.MULTILINE)
    ch3_research = re.findall(r"^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)", ch3, re.MULTILINE)
    if all_research != ch3_research:
        errors.append("experience_research_sections_outside_ch3")
    ch6 = exp[exp.index("## 6. 版本维护记录"):] if "## 6. 版本维护记录" in exp else ""
    if CORR_START in exp and CORR_START not in ch6:
        errors.append("experience_trade_corrections_outside_ch6")

    # Archive chapter-level placement of dynamic objective facts.
    arc5 = _between(arc, "## 5. 历史行情、成交与账户快照", "## 6. 历史Excel与专项数据来源")
    for start, end, name in (
        (REVIEW_FACT_START, REVIEW_FACT_END, "post_close_facts"),
        (TRADE_START, TRADE_END, "trade_events"),
        (ACCOUNT_START, ACCOUNT_END, "account_facts"),
    ):
        if start in arc and not (start in arc5 and end in arc5):
            errors.append(f"archive_managed_block_outside_ch5={name}")
    arc7 = arc[arc.index("## 7. 数据维护规则"):] if "## 7. 数据维护规则" in arc else ""
    if CORR_START in arc and CORR_START not in arc7:
        errors.append("archive_trade_corrections_outside_ch7")

    # Dashboard dynamic state must stay at the front, before static risk boundary.
    if "<!-- AUTO_STATE_SYNC_START -->" in dash and "## ETF策略风险口径" in dash:
        if dash.index("<!-- AUTO_STATE_SYNC_START -->") > dash.index("## ETF策略风险口径"):
            errors.append("dashboard_state_sync_not_front_loaded")
    return errors
