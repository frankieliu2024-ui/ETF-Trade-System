from __future__ import annotations

"""Semantic ownership validator for the formal human-readable fact files.

The validator protects section ownership and stable CASE identity. It does not
derive business facts, rewrite files, or require historical chapter renumbering.
"""

import json
import re
from pathlib import Path

EXPERIENCE = "ETF交易复盘与经验库_2026.md"
ARCHIVE = "ETF市场行情档案_2026.md"
DASHBOARD = "ETF当前状态_DASHBOARD.md"


def _between(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return ""
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def _inside(text: str, section: str, start: str, end: str) -> bool:
    return start not in text or (start in section and end in section)


def validate_files(root: Path) -> list[str]:
    errors: list[str] = []
    exp = (root / EXPERIENCE).read_text(encoding="utf-8")
    arc = (root / ARCHIVE).read_text(encoding="utf-8")
    dash = (root / DASHBOARD).read_text(encoding="utf-8")

    if re.findall(r"^## ([0-6])\. ", exp, re.MULTILINE) != list("0123456"):
        errors.append("experience_top_level_order")

    ch2 = _between(exp, "## 2. 真实交易CASE", "## 3. 历史研究与专项回测")
    ch4 = _between(exp, "## 4. OBS观察", "## 5. 研究与经验转化")
    ch6 = exp[exp.index("## 6. 版本维护记录"):] if "## 6. 版本维护记录" in exp else ""

    case_lines = re.findall(r"^### (2\.\d+) (CASE-(\d{8})-(\d{2}))[:：]", ch2, re.MULTILINE)
    raw_case_headings = re.findall(r"^### (?:2\.\d+ )?(CASE-(\d{8})-(\d{2}))[:：]", ch2, re.MULTILINE)
    if len(raw_case_headings) != len(case_lines):
        errors.append("case_heading_must_have_human_ordinal")
    ordinals = [int(item[0].split(".")[1]) for item in case_lines]
    case_ids = [item[1] for item in case_lines]
    if len(case_ids) != len(set(case_ids)):
        errors.append("experience_duplicate_case_identity")
    if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
        errors.append("experience_case_ordinal_order")
    if case_ids != sorted(case_ids):
        errors.append(f"experience_case_order={case_ids}")
    if "CASE目录与映射" in ch2 or "CASE详细记录" in ch2:
        errors.append("technical_case_navigation_heading_present")
    if not _inside(exp, ch2, "<!-- AUTO_CASE_INTAKE_START -->", "<!-- AUTO_CASE_INTAKE_END -->"):
        errors.append("case_intake_not_in_ch2")
    if not _inside(exp, ch4, "<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->", "<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->"):
        errors.append("post_close_reviews_not_in_ch4")
    if not _inside(exp, ch6, "<!-- AUTO_REVIEW_PREREQUISITE_UNAVAILABLE_START -->", "<!-- AUTO_REVIEW_PREREQUISITE_UNAVAILABLE_END -->"):
        errors.append("unavailable_reviews_not_in_ch6")
    if "CASE系统贡献索引" in ch2:
        errors.append("manual_case_contribution_index_present")

    ch3 = _between(exp, "## 3. 历史研究与专项回测", "## 4. OBS观察")
    all_research = re.findall(r"^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)", exp, re.MULTILINE)
    in_ch3 = re.findall(r"^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)", ch3, re.MULTILINE)
    if all_research != in_ch3:
        errors.append("experience_research_sections_outside_ch3")

    arc5 = _between(arc, "## 5. 历史行情、成交与账户快照", "## 6. 历史Excel与专项数据来源")
    for start, end, name in (
        ("<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->","<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->","post_close_facts"),
        ("<!-- AUTO_TRADE_EVENTS_START -->","<!-- AUTO_TRADE_EVENTS_END -->","trade_events"),
        ("<!-- AUTO_ACCOUNT_FACT_SYNC_START -->","<!-- AUTO_ACCOUNT_FACT_SYNC_END -->","account_facts"),
    ):
        if not _inside(arc, arc5, start, end):
            errors.append(f"archive_managed_block_outside_ch5={name}")

    arc7 = arc[arc.index("## 7. 数据维护规则"):] if "## 7. 数据维护规则" in arc else ""
    if "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" in exp and "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" not in ch6:
        errors.append("experience_trade_corrections_outside_ch6")
    if "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" in arc and "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" not in arc7:
        errors.append("archive_trade_corrections_outside_ch7")

    if "<!-- AUTO_STATE_SYNC_START -->" in dash and "## ETF策略风险口径" in dash and dash.index("<!-- AUTO_STATE_SYNC_START -->") > dash.index("## ETF策略风险口径"):
        errors.append("dashboard_state_sync_not_front_loaded")

    try:
        account = json.loads((root / "data/state/account_fact.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        account = {}
    auto_dash = _between(dash, "<!-- AUTO_STATE_SYNC_START -->", "<!-- AUTO_STATE_SYNC_END -->")
    current_structure = next((line for line in dash.splitlines() if "|ETF层当前结构|" in line), "")
    current_etf_codes = {
        str(position.get("code"))
        for position in account.get("positions") or []
        if position.get("asset_type") == "ETF" and position.get("code")
    }
    for code in sorted(current_etf_codes):
        if code not in auto_dash:
            errors.append(f"dashboard_current_holding_missing={code}")
    if re.search(r"（\d{6}）", current_structure):
        errors.append("dashboard_duplicate_current_role_projection")
    return errors
