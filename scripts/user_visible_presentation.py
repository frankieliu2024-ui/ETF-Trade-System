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
            expected = f"{name}（{code}）"
            if expected in content:
                continue
            errors.append(f"known security must use canonical display identity: {expected}")
            break
    return errors


def _presentation_structure_errors(text: str) -> list[str]:
    """Validate section-local numbering without forcing single paragraphs to be numbered."""
    errors: list[str] = []
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
        if re.match(r"^\\d+\\.\\S", stripped):
            errors.append("primary numbering requires a space after the marker")
        if re.match(r"^\\d+）\\S", stripped):
            # The frozen presentation contract permits the compact Chinese
            # marker 1） as well as 1） followed by a space.
            pass

        primary = re.match(r"^(\\d+)\\.\\s+", stripped)
        secondary = re.match(r"^(\\d+)）(?:\\s+)?\\S", stripped)
        if primary:
            if not current_heading:
                errors.append("numbered items must follow a section heading")
            flush_parent()
            value = int(primary.group(1))
            section_top.append(value)
            parent_top = value
            continue
        if secondary:
            if parent_top is None:
                errors.append("secondary numbering must follow a primary item")
            parent_secondary.append(int(secondary.group(1)))
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
        if current_heading and top_level_count == 1:
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
    """Require per-object coverage and section-local Chinese numbering for Formal Decision."""
    target_headings = {
        "【全市场发现逐只评估】",
        "【观察ETF逐只评估】",
        "【机会ETF逐只评估】",
        "【全部实际持仓】",
    }
    errors: list[str] = []
    current = ""
    entries: list[str] = []
    for line in text.splitlines() + ["【__END__】"]:
        stripped = line.strip()
        if stripped.startswith("【") and stripped.endswith("】"):
            if current in target_headings:
                numbered = [item for item in entries if re.match(r"^\d+）(?:\s+)?\S", item)]
                bad_primary = [item for item in entries if re.match(r"^\d+\.\s+", item)]
                bad_decimal = [item for item in entries if re.match(r"^\d+\.\d+\s+", item)]
                if bad_decimal:
                    errors.append(f"object section must not use decimal numbering: {current}")
                if bad_primary:
                    errors.append(f"object list must use secondary numbering 1）、2）、3）：{current}")
                if entries and not numbered:
                    errors.append(f"object section requires one separately numbered paragraph per object: {current}")
                if numbered:
                    nums = [int(re.match(r"^(\d+)）", item).group(1)) for item in numbered]
                    if nums != list(range(1, len(nums) + 1)):
                        errors.append(f"object numbering must restart at 1 and be sequential: {current}")
            current = stripped
            entries = []
            continue
        if current in target_headings and stripped:
            entries.append(stripped)
    return errors


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
    return [f"Formal Decision missing required section: {heading}" for heading in required if heading not in text]


def _time_presentation_errors(text: str) -> list[str]:
    errors: list[str] = []
    for line in text.splitlines():
        if "北京时间" in line and len(line.strip()) > 70:
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
    for line in str(section or "").splitlines():
        stripped = line.strip()
        numbered = bool(re.match(r"^(?:\d+[）)]|（\d+）)\s*", stripped))
        named = bool(re.match(r"^(?:\d+[）)]\s*|（\d+）\s*)?[^：\n]{1,50}（\d{6}）：", stripped))
        if numbered or named:
            if current:
                items.append("\n".join(current))
            current = [stripped]
        elif current:
            current.append(line)
    if current:
        items.append("\n".join(current))
    return items


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


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

    if not capital_section or not all(token in capital_section for token in ("现金", "机会", "持仓", "相较")):
        errors.append("formal_presentation_missing_whole_capital_comparison")
    if capital_section and not _has_any(capital_section, ("机会成本", "资本效率", "风险收益比", "边际收益风险")):
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
        if not _has_any(item, ("相较", "相比")) or not _has_any(item, ("现金", "替代用途")) or not _has_any(item, ("机会", "替代用途")):
            errors.append(prefix + "missing_hold_cash_opportunity_comparison")
        if not _has_any(item, ("若", "如果", "当")):
            errors.append(prefix + "missing_change_condition")
        if not _has_any(item, ("继续持有", "持有管理")):
            errors.append(prefix + "missing_hold_alternative")
        if "追加" not in item:
            errors.append(prefix + "missing_add_alternative")
        if not _has_any(item, ("部分减持", "部分减仓", "减持")):
            errors.append(prefix + "missing_reduce_alternative")
        if not _has_any(item, ("全部退出", "全部清仓", "全部卖出", "退出")):
            errors.append(prefix + "missing_exit_alternative")
        if not _has_any(item, ("如果当前没有", "若当前没有", "假设当前没有", "零持仓", "重新配置资本", "按当前价格重新买入", "重新买入")):
            errors.append(prefix + "missing_zero_base_reentry_counterfactual")
        holding_action = _has_any(item, ("继续持有", "持有管理")) and not _has_any(item, ("建议减持", "建议退出", "建议全部退出", "建议全部清仓"))
        if holding_action:
            if not _has_any(item, ("继续占资", "继续配置", "保留该持仓")) or not _has_any(item, ("优于", "胜出", "更优")):
                errors.append(prefix + "missing_affirmative_hold_over_release_reason")
            if "迁移成本" not in item:
                errors.append(prefix + "missing_migration_cost_review")
            if not _has_any(item, ("失败风险", "失败损失")):
                errors.append(prefix + "missing_failure_risk_review")
            if not _has_any(item, ("右尾", "尾部收益", "上行尾部")):
                errors.append(prefix + "missing_winner_right_tail_review")
            if "账户功能" not in item:
                errors.append(prefix + "missing_account_function_review")

    # Compare all three deployment alternatives for every opportunity object,
    # rather than allowing one generic paragraph to cover an entire section.
    opportunity_items = _formal_holding_items(opportunity_section)
    if not opportunity_items and not _has_any(opportunity_section, ("0只", "未形成机会对象", "暂无机会对象", "没有形成机会对象")):
        errors.append("formal_presentation_opportunity_missing_explicit_zero_or_object_count")
    if not opportunity_items:
        opportunity_items = [opportunity_section]
    for index, item in enumerate(opportunity_items, start=1):
        prefix = f"formal_presentation_opportunity_{index}_"
        if "Trial" not in item:
            errors.append(prefix + "missing_trial_review")
        if "Confirm" not in item:
            errors.append(prefix + "missing_direct_confirm_review")
        if "0元" not in item:
            errors.append(prefix + "missing_zero_deployment_comparison")
        if not _has_any(item, ("5,000", "5000", "5千元", "5,000元")):
            errors.append(prefix + "missing_trial_amount_standard")
        if not _has_any(item, ("未达到", "未成立", "决定性缺口", "尚缺", "不具备", "缺少", "已达到")):
            errors.append(prefix + "missing_eligibility_reason")
        if not _has_any(item, ("下一复核节点", "下次正式决策", "下一次正式决策", "最近复核节点", "下一节点")):
            errors.append(prefix + "missing_next_review_node")

    if not _has_any(text, ("1）", "2）", "3）", "（1）", "（2）", "（3）")):
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
