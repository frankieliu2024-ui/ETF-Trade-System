from __future__ import annotations

"""Lightweight semantic validator for human-readable formal fact files.

Writers place managed blocks at their canonical anchors. This module only guards
against regression; it does not rewrite files or repair history during runtime.
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


def _block_inside(text: str, section: str, start: str, end: str) -> bool:
    return start not in text or (start in section and end in section)


def validate_files(root: Path) -> list[str]:
    errors: list[str] = []
    exp = (root / EXPERIENCE).read_text(encoding="utf-8")
    arc = (root / ARCHIVE).read_text(encoding="utf-8")
    dash = (root / DASHBOARD).read_text(encoding="utf-8")

    exp_top = re.findall(r"^## ([0-6])\. ", exp, re.MULTILINE)
    if exp_top != list("0123456"):
        errors.append(f"experience_top_level_order={exp_top}")

    ch2 = _between(exp, "## 2. 真实交易CASE", "## 3. 历史研究与专项回测")
    nums = re.findall(r"^### (2\.\d+)\b", ch2, re.MULTILINE)
    if len(nums) != len(set(nums)):
        errors.append("experience_duplicate_chapter2_numbers")
    cases = re.findall(r"^### (2\.\d+) (CASE-(\d{8})-(\d{2}))[:：]", ch2, re.MULTILINE)
    case_ids = [x[1] for x in cases]
    if case_ids != sorted(case_ids):
        errors.append(f"experience_case_order={case_ids}")
    expected = [f"2.{i}" for i in range(3, 3 + len(cases))]
    if [x[0] for x in cases] != expected:
        errors.append("experience_case_section_numbers")
    contrib = re.search(r"^### (2\.\d+) CASE系统贡献索引", ch2, re.MULTILINE)
    if not contrib or contrib.group(1) != f"2.{3 + len(cases)}":
        errors.append("experience_contribution_index")
    for start, end, name in (("<!-- AUTO_CASE_INTAKE_START -->","<!-- AUTO_CASE_INTAKE_END -->","case_intake"),("<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->","<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->","post_close_cases")):
        if not _block_inside(exp, ch2, start, end):
            errors.append(f"experience_managed_block_outside_ch2={name}")

    ch3 = _between(exp, "## 3. 历史研究与专项回测", "## 4. OBS观察")
    all_research = re.findall(r"^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)", exp, re.MULTILINE)
    in_ch3 = re.findall(r"^### (2026-\d{2}-\d{2}[^\n]*(?:专项|勾稽修正)[^\n]*)", ch3, re.MULTILINE)
    if all_research != in_ch3:
        errors.append("experience_research_sections_outside_ch3")

    arc5 = _between(arc, "## 5. 历史行情、成交与账户快照", "## 6. 历史Excel与专项数据来源")
    for start, end, name in (("<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->","<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->","post_close_facts"),("<!-- AUTO_TRADE_EVENTS_START -->","<!-- AUTO_TRADE_EVENTS_END -->","trade_events"),("<!-- AUTO_ACCOUNT_FACT_SYNC_START -->","<!-- AUTO_ACCOUNT_FACT_SYNC_END -->","account_facts")):
        if not _block_inside(arc, arc5, start, end):
            errors.append(f"archive_managed_block_outside_ch5={name}")

    exp6 = exp[exp.index("## 6. 版本维护记录"):] if "## 6. 版本维护记录" in exp else ""
    arc7 = arc[arc.index("## 7. 数据维护规则"):] if "## 7. 数据维护规则" in arc else ""
    if "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" in exp and "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" not in exp6:
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
    current_structure = next((line for line in dash.splitlines() if line.startswith("|ETF层当前结构|")), "")
    for position in account.get("positions") or []:
        if position.get("asset_type") != "ETF" or not position.get("code"):
            continue
        code = str(position["code"])
        if code not in auto_dash:
            errors.append(f"dashboard_current_holding_missing={code}")
        if current_structure and "观察ETF" in current_structure and code in current_structure:
            errors.append(f"dashboard_holding_observation_conflict={code}")
    return errors
