from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CLOSURE_PHRASES = ("本事项已闭环", "本事项结束，不再追加维护")
INTERNAL_MARKERS = (
    "request_id", "request id", "workflow run", "run_id", "run id",
    "provider log", "commit sha", "head sha",
)

# High-confidence control-plane vocabulary that has a plain business-language
# equivalent. Keep this list deliberately bounded: it protects presentation
# without becoming a general English/NLP blacklist or changing machine schemas.
CONTROL_PLANE_PATTERNS = (
    (r"(?i)(?<![\w-])request-bound(?![\w-])", "request-bound"),
    (r"(?<![A-Z0-9_])READY(?![A-Z0-9_])", "READY"),
    (r"(?i)(?<![\w-])blocker(?:s)?(?![\w-])", "blocker"),
    (r"(?<![A-Z0-9_])NO_ADD(?![A-Z0-9_])", "NO_ADD"),
    (r"(?<![A-Z0-9_])BUSINESS_DECISION_READY(?![A-Z0-9_])", "BUSINESS_DECISION_READY"),
    (r"(?i)(?<![\w-])reply_freezable(?![\w-])", "reply_freezable"),
    (r"(?i)(?<![\w-])canonical\s+persistence(?![\w-])", "canonical persistence"),
    (r"(?i)(?<![\w-])canonical(?![\w-])", "canonical"),
    (r"(?i)(?<![\w-])Discovery(?![\w-])", "Discovery"),
    (r"(?i)(?<![\w-])Observation\s+(?:ADMIT|RETAIN|EXIT)(?![\w-])", "Observation state enum"),
    (r"(?<![A-Z0-9_])(?:ADMIT|RETAIN|EXIT)(?![A-Z0-9_])", "ADMIT/RETAIN/EXIT"),
)


def canonical_security_map(root: Path | None = None) -> dict[str, str]:
    """Build a read-only display map from existing canonical/config facts."""
    root = (root or ROOT).resolve()
    mapping: dict[str, str] = {}
    sources = (
        root / "config/market/etf_monitor_universe.json",
        root / "data/state/account_fact.json",
        root / "data/state/asset_roles.json",
    )
    for path in sources:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "etf_monitor_universe.json":
            rows = data.get("objects") or []
        elif path.name == "account_fact.json":
            rows = data.get("positions") or []
        else:
            rows = [
                {"code": code, "name": body.get("name")}
                for code, body in (data.get("roles") or {}).items()
                if isinstance(body, dict)
            ]
        for row in rows:
            code = str((row or {}).get("code") or "").strip()
            name = str((row or {}).get("name") or "").strip()
            if code and name:
                mapping[code] = name
    return mapping


def _security_identity_errors(content: str, security_map: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for code, name in security_map.items():
        for match in re.finditer(rf"(?<!\d){re.escape(code)}(?!\d)", content):
            # Formal Decision object labels may append quantity inside the same
            # full-width parentheses; the canonical identity is still name+code.
            expected_prefix = f"{name}（{code}"
            expected = f"{name}（{code}）"
            if re.search(rf"{re.escape(name)}（{re.escape(code)}(?:，[^）]*)?）", content):
                continue
            errors.append(f"known security must use canonical display identity: {expected}")
            break
    return errors


def _presentation_structure_errors(text: str) -> list[str]:
    """Validate section-local primary/secondary numbering."""
    errors: list[str] = []
    is_formal = text.startswith("【ETF正式决策】")
    current_heading = ""
    section_top: list[int] = []
    parent_top: int | None = None
    parent_secondary: list[int] = []

    def flush_parent() -> None:
        nonlocal parent_secondary
        if parent_secondary:
            expected = list(range(1, len(parent_secondary) + 1))
            if parent_secondary != expected:
                errors.append(
                    f"secondary numbering must restart at 1 and be sequential under one primary item: {current_heading}"
                )
        parent_secondary = []

    def flush_section() -> None:
        nonlocal section_top, parent_top
        flush_parent()
        if section_top and section_top != list(range(1, len(section_top) + 1)):
            errors.append(
                f"primary numbering must restart at 1 and be sequential within one section: {current_heading}"
            )
        section_top = []
        parent_top = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("【") and stripped.endswith("】"):
            flush_section()
            current_heading = stripped
            continue
        if re.match(r"^\d+\.\S", stripped):
            errors.append("primary numbering requires a space after the marker")
        if not is_formal and re.match(r"^(?:（\d+）|\d+）)\S", stripped):
            errors.append("secondary numbering requires a space after the marker")

        primary = re.match(r"^(\d+)\.\s+\S", stripped)
        secondary = re.match(r"^(?:（(\d+)）|(\d+)）)(?:\s+)?\S", stripped)
        if primary:
            flush_parent()
            section_top.append(int(primary.group(1)))
            parent_top = int(primary.group(1))
            continue
        if secondary:
            if parent_top is None:
                errors.append("secondary numbering must follow a primary item")
            parent_secondary.append(int(secondary.group(1) or secondary.group(2)))
            continue

    flush_section()
    return list(dict.fromkeys(errors))

def _single_item_numbering_errors(text: str) -> list[str]:
    """Reject artificial numbering when a section has one complete item."""
    errors: list[str] = []
    current_heading = ""
    top_level_count = 0

    def flush() -> None:
        nonlocal top_level_count
        object_sections = {
            "【全市场发现逐只评估】",
            "【全市场发现】",
            "【观察ETF逐只评估】",
            "【观察ETF】",
            "【机会ETF逐只评估】",
            "【机会ETF】",
            "【全部实际持仓】",
        }
        if current_heading and current_heading not in object_sections and top_level_count == 1:
            errors.append(
                f"single-item section must not use primary numbering: {current_heading}"
            )
        top_level_count = 0

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("【") and stripped.endswith("】"):
            flush()
            current_heading = stripped
            continue
        if re.match(r"^\d+\.\s+", stripped):
            top_level_count += 1
    flush()
    return errors


def _formal_object_numbering_errors(text: str) -> list[str]:
    """Require Formal Decision object paragraphs and their nested analysis items."""
    if not text.startswith("【ETF正式决策】") and not any(
        text.startswith(heading)
        for heading in (
            "【全市场发现逐只评估】",
            "【全市场发现】",
            "【观察ETF逐只评估】",
            "【观察ETF】",
            "【机会ETF逐只评估】",
            "【机会ETF】",
            "【全部实际持仓】",
        )
    ):
        return []
    target_headings = {
        "【全市场发现逐只评估】",
        "【全市场发现】",
        "【观察ETF逐只评估】",
        "【观察ETF】",
        "【机会ETF逐只评估】",
        "【机会ETF】",
        "【全部实际持仓】",
    }
    errors: list[str] = []
    current = ""
    entries: list[str] = []

    def flush() -> None:
        if current not in target_headings:
            return
        primary = [item for item in entries if re.match(r"^\d+\.\s+\S", item)]
        legacy_object = [
            item for item in entries
            if re.match(r"^\d+）(?:\s+)?[^：\n]{1,80}（\d{6}）：", item)
        ]
        decimal = [item for item in entries if re.match(r"^\d+\.\d+\s+", item)]
        explicit_zero = (
            current == "【机会ETF逐只评估】"
            and _has_any(
                "\n".join(entries),
                ("0只", "未形成机会对象", "暂无机会对象", "没有形成机会对象"),
            )
        )
        if decimal:
            errors.append(f"object section must not use decimal numbering: {current}")
        if legacy_object:
            errors.append(f"object list must use primary numbering 1., 2., 3.：{current}")
        if entries and not primary and not explicit_zero:
            errors.append(f"object section requires one separately numbered paragraph per object: {current}")
        if primary:
            nums = [int(re.match(r"^(\d+)\.", item).group(1)) for item in primary]
            if nums != list(range(1, len(nums) + 1)):
                errors.append(f"object numbering must restart at 1 and be sequential: {current}")
            primary_positions = [
                index for index, item in enumerate(entries)
                if re.match(r"^\d+\.\s+\S", item)
            ]
            for position, start in enumerate(primary_positions):
                end = primary_positions[position + 1] if position + 1 < len(primary_positions) else len(entries)
                has_secondary = any(
                    re.match(r"^(?:（\d+）|\d+）)(?:\s+)?\S", item)
                    for item in entries[start + 1:end]
                )
                if not has_secondary:
                    errors.append(
                        f"each object must contain separately numbered secondary analysis items: {current}"
                    )

    for line in text.splitlines() + ["【__END__】"]:
        stripped = line.strip()
        if stripped.startswith("【") and stripped.endswith("】"):
            flush()
            current = stripped
            entries = []
            continue
        if current in target_headings and stripped:
            entries.append(stripped)
    return list(dict.fromkeys(errors))

def _formal_required_sections_errors(text: str) -> list[str]:
    if not text.startswith("【ETF正式决策】"):
        return []
    required = (
        "【判断时点与事实边界】", "【当前正式判断】", "【风险与市场状态】",
        "【三层监测】", "【全市场发现逐只评估】", "【观察ETF逐只评估】",
        "【机会ETF逐只评估】", "【全市场机会与资本竞争】", "【现金管理】",
        "【全部实际持仓】", "【最终管理动作】", "【关键风险与改变判断的条件】",
        "【信息不足在哪里】", "【执行边界】", "【置信度】", "【最可能出错的地方】",
    )
    aliases = {
        "【三层监测】": "【市场监测】",
        "【全市场发现逐只评估】": "【全市场发现】",
        "【观察ETF逐只评估】": "【观察ETF】",
        "【机会ETF逐只评估】": "【机会ETF】",
    }
    errors = [
        f"Formal Decision missing required section: {heading}"
        for heading in required
        if heading not in text and (heading not in aliases or aliases[heading] not in text)
    ]
    if "【后置交接状态】" in text:
        errors.append("technical handoff state must not be included in Formal Decision body")
    return errors


def _time_presentation_errors(text: str) -> list[str]:
    errors: list[str] = []
    for line in text.splitlines():
        if "北京时间" in line and len(line.strip()) > 70 and not text.startswith("【ETF正式决策】"):
            errors.append("Beijing-time statement is too verbose")
    return errors


def _internal_presentation_terms(text: str) -> list[str]:
    terms = (
        "workflow", "completion", "notification_center", "PushPlus",
        "provider", "content_hash", "BDS", "DWP", "PIT",
        "query_context", "decision_work_package", "系统时钟", "执行环境",
        "UTC+08:00", "DEGRADED", "FULL_REPORT", "STATE_SYNC_ONLY",
        # Machine-schema fields must never leak into the business report.
        "state_name", "capital_action", "remaining_deployable_cash",
        "why_not_selected", "opportunity_cost_if_selected",
        "zero_amount_decisive_reason_if_zero", "new_amount_yuan",
        "no_trade_authority", "formal_completion_path",
    )
    return [term for term in terms if re.search(re.escape(term), text, re.IGNORECASE)]



def _formal_section_content(content: str, heading: str) -> str:
    aliases = {
        "【三层监测】": "【市场监测】",
        "【全市场发现逐只评估】": "【全市场发现】",
        "【观察ETF逐只评估】": "【观察ETF】",
        "【机会ETF逐只评估】": "【机会ETF】",
    }
    accepted = {heading, aliases.get(heading)}
    lines = str(content or "").splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() in accepted), None)
    if start is None:
        return ""
    end = len(lines)
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("【") and stripped.endswith("】"):
            end = i
            break
    return "\n".join(lines[start:end])


def _formal_holding_items(section: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    # Formal object headers may include quantity after the canonical code:
    # 名称（代码，数量）. The quantity does not change object identity.
    # Production reports commonly put the first analysis sentence on the same
    # line as the object header, so match the header prefix rather than
    # requiring the line to end immediately after the identity.
    identity = r"（\d{6}(?:，[^）]*)?）"
    header = rf"^(?:\d+\.\s+|\d+[）)]\s*|（\d+）\s*)?[^：\n]{{1,80}}{identity}(?:：|$)"
    for line in str(section or "").splitlines():
        stripped = line.strip()
        if re.match(header, stripped):
            if current:
                items.append("\n".join(current))
            current = [stripped]
        elif current:
            current.append(line)
    if current:
        items.append("\n".join(current))
    return items


def _hyperliquid_placement_errors(content: str) -> list[str]:
    """Keep Hyperliquid node metadata and object conclusions in their owners.

    The market-monitoring section is an aggregate evidence summary.  It must not
    become a second Hyperliquid report or a substitute for the four dedicated
    object sections.  This is intentionally a placement check, not a market
    judgment and not a requirement to infer missing facts.
    """
    text = normalize_formal_decision_content(content)
    if "Hyperliquid" not in text:
        return []
    formal = text.startswith("【ETF正式决策】")
    if formal:
        boundary_heading = "【判断时点与事实边界】"
        monitor_heading = "【市场监测】"
        if monitor_heading not in text:
            monitor_heading = "【三层监测】"
    elif text.startswith("【ETF交易复盘】"):
        boundary_heading = "【复盘与数据时点（北京时间）】"
        monitor_heading = "【三层监测】"
    else:
        return []

    boundary = _formal_section_content(text, boundary_heading)
    monitor = _formal_section_content(text, monitor_heading)
    errors: list[str] = []
    metadata_terms = (
        "读取状态", "采集时间", "事实身份", "context_id", "时间边界", "质量状态",
        "观测数量", "覆盖范围", "映射状态", "ETF映射", "刷新失败", "无实时观测",
    )
    if not boundary:
        errors.append("hyperliquid_metadata_missing_fact_boundary_section")
    elif not _has_any(boundary, ("Hyperliquid",)):
        errors.append("hyperliquid_metadata_missing_fact_boundary_statement")
    leaked_metadata = [term for term in metadata_terms if term in monitor]
    if leaked_metadata:
        errors.append("hyperliquid_metadata_must_not_be_in_monitoring_section:" + ",".join(leaked_metadata))

    # Object-level conclusions may be mentioned in monitoring only as an
    # aggregate cross-market summary, never as the object's direct action.
    # Dedicated sections are checked for the presence of an explicit category
    # when the report actually presents a Hyperliquid object conclusion.
    object_sections = (
        ("【全市场发现】", "【全市场发现逐只评估】"),
        ("【观察ETF】", "【观察ETF逐只评估】"),
        ("【机会ETF】", "【机会ETF逐只评估】"),
        ("【全部实际持仓】", "【全部实际持仓】"),
    )
    direct_conclusion_terms = ("支持", "矛盾", "时机过滤", "拥挤", "定价偏离", "无法用于判断")
    has_direct_conclusion = _has_any(text, direct_conclusion_terms)
    if has_direct_conclusion:
        present = 0
        for primary, alias in object_sections:
            if primary in text or alias in text:
                section = _formal_section_content(text, primary if primary in text else alias)
                if "Hyperliquid" in section and _has_any(section, direct_conclusion_terms):
                    present += 1
        if present == 0:
            errors.append("hyperliquid_object_conclusion_missing_dedicated_object_section")
    return errors


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _explicit_reentry_answer(item: str) -> bool:
    """Check for an explicit, reasoned answer to the zero-position question.

    This is a coverage check; it cannot establish the truth of market evidence.
    """
    triggers = ("如果当前没有", "若当前没有", "假设当前没有", "零持仓", "按当前价格重新买入", "从零进入", "从零重新进入", "从零重新配置")
    for sentence in re.split(r"[。；;\\n]", item):
        if not any(term in sentence for term in triggers):
            continue
        # Merely asking whether to buy or saying that it needs comparing
        # is not a decision. Require an explicit yes/no commitment.
        if not re.search(
            r"(?:不会|不愿意|不选择|不考虑|不买入|不配置|暂不|不再)"
            r"[^。；;\\n]{0,24}(?:买入|配置|建仓|持有|投入)"
            r"|(?:会|愿意|选择|决定|建议|应当)"
            r"[^。；;\\n]{0,24}(?:重新买入|买入|配置|建仓|持有|投入)"
            r"|(?:愿意|不愿意|会|不会)[^。；;\\n]{0,16}重新配置资本"
            r"|(?:从零进入|从零重新进入|从零重新配置)[^。；;\\n]{0,24}(?:不优|不具备|需重新证明|需新的)",
            sentence,
        ):
            continue
        # The explanation must contain substantive text after the causal link.
        reason = re.search(r"(?:因为|由于|基于|考虑到|鉴于)([^，,：:]{4,})", sentence)
        if reason and re.search(r"[\u4e00-\u9fff]{3,}", reason.group(1)):
            return True
        # Existing compliant reports can state the reason before the
        # zero-position decision, linked by a semicolon or full stop.
        preceding = item[:item.find(sentence)].rstrip("。；;\\n ")
        previous = re.split(r"[。；;\\n]", preceding)[-1]
        if previous and re.search(r"(?:因为|由于|基于|考虑到|鉴于)[^。；;\\n]{6,}", previous):
            return True
    return False


def formal_decision_semantic_errors(content: str) -> list[str]:
    """Shared semantic coverage gate for Formal Decision freeze and report delivery.

    This checks explicit counterfactual coverage in the user-visible report. It
    does not infer market truth, choose trades, or replace the actor's judgment.
    """
    text = normalize_formal_decision_content(content)
    if not text.startswith("【ETF正式决策】"):
        return []

    risk_section = _formal_section_content(text, "【风险与市场状态】")
    capital_section = _formal_section_content(text, "【全市场机会与资本竞争】")
    opportunity_section = _formal_section_content(text, "【机会ETF逐只评估】")
    holdings_section = _formal_section_content(text, "【全部实际持仓】")
    errors: list[str] = []

    if not risk_section or "风险许可" not in risk_section:
        errors.append("formal_presentation_missing_risk_permission_reasoning")

    if not capital_section or not all(_has_any(capital_section, group) for group in (("现金",), ("机会",), ("持仓",), ("相较", "比较", "竞争", "高于", "低于"))):
        errors.append("formal_presentation_missing_whole_capital_comparison")
    if capital_section and not _has_any(capital_section, ("机会成本", "资本效率", "风险收益比", "边际收益风险", "流动性", "确定性", "迁移成本", "边际收益", "失败风险")):
        errors.append("formal_presentation_missing_capital_efficiency_comparison")

    capital_alternatives = (
        ("现金", "保留"),
        ("持仓", "持有"),
        ("追加",),
        ("减持", "释放"),
        ("全部退出", "退出"),
        ("机会",),
    )
    if capital_section and not all(_has_any(capital_section, group) for group in capital_alternatives):
        errors.append("formal_presentation_missing_capital_alternatives")
    if capital_section and not _has_any(capital_section, ("因为", "由于", "原因", "依据", "证据", "尚未", "不足", "优于", "弱于", "条件")):
        errors.append("formal_presentation_missing_capital_choice_rationale")
    if capital_section and not _has_any(capital_section, ("当前胜出", "胜出用途", "最有效用途", "下一单位资本")):
        errors.append("formal_presentation_missing_selected_capital_use")
    if "新增部署0元" in text or "实际新增部署0元" in text:
        if not _has_any(capital_section, ("最近可执行候选", "最近候选", "不存在合理的最近可执行候选")):
            errors.append("formal_presentation_zero_deployment_missing_nearest_candidate")
        if not _has_any(capital_section, ("决定性缺口", "决定性证据缺口", "尚缺", "证据不足")):
            errors.append("formal_presentation_zero_deployment_missing_decisive_evidence_gap")
        if not _has_any(capital_section, ("下一复核节点", "下次正式决策", "下一次正式决策", "最近复核节点", "下一节点")):
            errors.append("formal_presentation_zero_deployment_missing_next_review_node")

    holding_items = _formal_holding_items(holdings_section)
    if not holding_items:
        errors.append("formal_presentation_missing_individual_position_reviews")
    for index, item in enumerate(holding_items, start=1):
        prefix = f"formal_presentation_position_{index}_"
        if not _has_any(item, ("相较", "相比", "比较", "竞争")) or not _has_any(item, ("现金", "替代用途")) or not _has_any(item, ("机会", "替代用途")):
            errors.append(prefix + "missing_hold_cash_opportunity_comparison")
        if not _has_any(item, ("若", "如果", "当")):
            errors.append(prefix + "missing_change_condition")
        if not _has_any(item, ("继续持有", "持有管理", "持有观察", "小仓持有", "保留持仓", "按其他证券持有")):
            errors.append(prefix + "missing_hold_alternative")
        if "追加" not in item:
            errors.append(prefix + "missing_add_alternative")
        if not _has_any(item, ("部分减持", "部分减仓", "减持")):
            errors.append(prefix + "missing_reduce_alternative")
        if not _has_any(item, ("全部退出", "全部清仓", "全部卖出", "退出")):
            errors.append(prefix + "missing_exit_alternative")
        if not _explicit_reentry_answer(item):
            errors.append(prefix + "missing_answered_zero_base_reentry_counterfactual")
        holding_action = _has_any(item, ("继续持有", "持有管理", "持有观察", "小仓持有", "保留持仓", "按其他证券持有")) and not _has_any(item, ("建议减持", "建议退出", "建议全部退出", "建议全部清仓"))
        if holding_action:
            if not _has_any(item, ("继续占资", "继续配置", "保留该持仓")) or not _has_any(item, ("优于", "胜出", "更优")):
                errors.append(prefix + "missing_affirmative_hold_over_release_reason")
            if "迁移成本" not in item:
                errors.append(prefix + "missing_migration_cost_review")
            if not _has_any(item, ("失败风险", "失败损失")):
                errors.append(prefix + "missing_failure_risk_review")
            if not _has_any(item, ("右尾", "尾部收益", "上行尾部")):
                errors.append(prefix + "missing_winner_right_tail_review")
            if not _has_any(item, ("账户功能", "分散功能", "仓位功能")):
                errors.append(prefix + "missing_account_function_review")

    # Compare all three deployment alternatives for every opportunity object,
    # rather than allowing one generic paragraph to cover an entire section.
    opportunity_items = _formal_holding_items(opportunity_section)
    explicit_zero_opportunities = _has_any(
        opportunity_section,
        ("0只", "未形成机会对象", "暂无机会对象", "没有形成机会对象"),
    )
    if not opportunity_items and not explicit_zero_opportunities:
        errors.append("formal_presentation_opportunity_missing_explicit_zero_or_object_count")
    if not opportunity_items and explicit_zero_opportunities:
        if not _has_any(opportunity_section, ("Trial", "试探性部署")):
            errors.append("formal_presentation_zero_opportunity_missing_trial_boundary")
        if not _has_any(opportunity_section, ("Confirm", "确认部署")):
            errors.append("formal_presentation_zero_opportunity_missing_confirm_boundary")
        if "0元" not in opportunity_section:
            errors.append("formal_presentation_zero_opportunity_missing_zero_deployment")
    for index, item in enumerate(opportunity_items, start=1):
        prefix = f"formal_presentation_opportunity_{index}_"
        if not _has_any(item, ("Trial", "试探性部署")):
            errors.append(prefix + "missing_trial_review")
        if not _has_any(item, ("Confirm", "确认部署")):
            errors.append(prefix + "missing_direct_confirm_review")
        if "0元" not in item:
            errors.append(prefix + "missing_zero_deployment_comparison")
        if not _has_any(item, ("5,000", "5000", "5千元", "5,000元")):
            errors.append(prefix + "missing_trial_amount_standard")
        if not _has_any(item, ("未达到", "未成立", "决定性缺口", "尚缺", "不具备", "缺少", "已达到")):
            errors.append(prefix + "missing_eligibility_reason")
        if not _has_any(item, ("下一复核节点", "下次正式决策", "下一次正式决策", "最近复核节点", "下一节点")):
            errors.append(prefix + "missing_next_review_node")

    if not re.search(r"(?m)^(?:\d+\.\s+|（\d+）(?:\s+)?\S)", text):
        errors.append("formal_presentation_missing_secondary_numbering")
    return errors

def validate_user_visible_content(
    content: str,
    *,
    canonical_closure_confirmed: bool = False,
    technical_audit_mode: bool = False,
    security_map: dict[str, str] | None = None,
    root: Path | None = None,
) -> None:
    """Fail closed on user-visible presentation drift; never rewrite prose."""
    text = normalize_formal_decision_content(content)
    if not text:
        raise ValueError("user-visible content must not be empty")
    mapping = security_map if security_map is not None else canonical_security_map(root)
    errors = _security_identity_errors(text, mapping)
    errors.extend(_presentation_structure_errors(text))
    errors.extend(_single_item_numbering_errors(text))
    errors.extend(_formal_object_numbering_errors(text))
    errors.extend(_formal_required_sections_errors(text))
    errors.extend(formal_decision_semantic_errors(text))
    errors.extend(_hyperliquid_placement_errors(text))
    errors.extend(_time_presentation_errors(text))
    if not technical_audit_mode:
        for term in _internal_presentation_terms(text):
            errors.append(f"internal execution term is forbidden in normal business presentation: {term}")
    if not canonical_closure_confirmed:
        for phrase in CLOSURE_PHRASES:
            if phrase in text:
                errors.append(f"closure phrase requires canonical persistence/readback acceptance: {phrase}")
    if not technical_audit_mode:
        lower = text.lower()
        for marker in INTERNAL_MARKERS:
            if marker in lower:
                errors.append(f"internal identity is forbidden in normal business presentation: {marker}")
        for pattern, label in CONTROL_PLANE_PATTERNS:
            if re.search(pattern, text):
                errors.append(
                    f"control-plane vocabulary must be translated into business language: {label}"
                )
    if errors:
        raise ValueError("user-visible presentation contract failed: " + "; ".join(dict.fromkeys(errors)))


def normalize_formal_decision_content(content: str) -> str:
    """Return one exact business presentation without duplicated title lines.

    The actor may supply a title plus an identical first body line. The user
    contract has one visible title, so remove only an immediately repeated
    first non-empty line; never rewrite the report body.
    """
    text = str(content or "").strip()
    if not text:
        return ""
    # Model-generated reports occasionally leak a markdown escape backslash at
    # the end of a line ("\\\\\\n"). It has no business meaning and must not
    # reach the frozen user-visible body or its content hash.
    text = re.sub(r"\\(?=\\n)", "", text)
    lines = text.splitlines()
    first = lines[0].strip()
    if first and len(lines) > 1 and lines[1].strip() == first:
        lines = [lines[0]] + lines[2:]
    return "\n".join(lines).strip()


def validate_formal_decision_reply_nonblocking(
    content: str,
    *,
    canonical_closure_confirmed: bool = False,
    technical_audit_mode: bool = False,
    security_map: dict[str, str] | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    """Validate presentation without revoking an already-earned BUSINESS_DECISION_READY reply.

    This helper is deliberately side-effect free and performs no network, persistence,
    projection, notification, or canonical writes. Presentation defects are returned
    for caller-visible correction/audit; they are not a new business-decision gate.
    """
    try:
        validate_user_visible_content(
            content,
            canonical_closure_confirmed=canonical_closure_confirmed,
            technical_audit_mode=technical_audit_mode,
            security_map=security_map,
            root=root,
        )
    except ValueError as exc:
        return {
            "business_reply_eligible": True,
            "presentation_valid": False,
            "presentation_error": str(exc),
        }
    return {
        "business_reply_eligible": True,
        "presentation_valid": True,
        "presentation_error": "",
    }



def build_formal_decision_presentation_binding(
    content: str,
    *,
    request_id: str,
    decision_id: str,
    parent_request_id: str = "",
    report_id: str = "",
    canonical_closure_confirmed: bool = False,
    technical_audit_mode: bool = False,
    security_map: dict[str, str] | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    """Return a deterministic binding for the exact Formal Decision reply text.

    The business decision remains independently eligible; this binding only
    records whether the proposed user-visible text satisfies the presentation
    contract and fingerprints the exact text that was checked.
    """
    safe_request_id = str(request_id or "").strip()
    safe_decision_id = str(decision_id or "").strip()
    if not safe_request_id or not safe_decision_id:
        raise ValueError("formal decision presentation binding requires request_id and decision_id")
    result = validate_formal_decision_reply_nonblocking(
        content,
        canonical_closure_confirmed=canonical_closure_confirmed,
        technical_audit_mode=technical_audit_mode,
        security_map=security_map,
        root=root,
    )
    import hashlib
    text = normalize_formal_decision_content(content)
    result.update({
        "schema_version": "formal-decision-presentation-binding-v1",
        "request_id": safe_request_id,
        "parent_request_id": str(parent_request_id or safe_request_id).strip(),
        "decision_id": safe_decision_id,
        "report_id": str(report_id or f"etf-formal-decision:{safe_decision_id}").strip(),
        "full_content": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "technical_audit_mode": bool(technical_audit_mode),
    })
    return result

