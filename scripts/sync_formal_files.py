from __future__ import annotations

"""Minimal idempotent maintenance for confirmed account facts.

This module updates only human-readable fact documents plus the account reference
embedded in CURRENT. It never writes rule or permission documents and never
derives orders or lifecycle actions. It is invoked after an accepted state-sync
request, so account_fact remains the machine source of truth while CURRENT keeps
an up-to-date account pointer after the core market snapshot has already been
published.
"""

import argparse
import json
import os
import re
from datetime import timezone, timedelta
from pathlib import Path

try:
    from confirmed_trade_facts import effective_confirmed_fee_fact
except ModuleNotFoundError:
    # The production workflow imports this module as ``scripts.sync_formal_files``;
    # keep the repository-local dependency resolvable in both direct-script and
    # package-style execution without changing the projection contract.
    from scripts.confirmed_trade_facts import effective_confirmed_fee_fact
try:
    from build_stock_context import active_account_asset_codes, normalize_code, position_metric
except ModuleNotFoundError:
    from scripts.build_stock_context import active_account_asset_codes, normalize_code, position_metric
try:
    from formal_file_mutation_gateway import (
        replace_managed_block as replace_block,
        write_formal_text_if_changed,
    )
except ModuleNotFoundError:
    from scripts.formal_file_mutation_gateway import (
        replace_managed_block as replace_block,
        write_formal_text_if_changed,
    )
try:
    from research_artifact_contract import archive_completed_research
except ModuleNotFoundError:
    from scripts.research_artifact_contract import archive_completed_research
try:
    from lifecycle_state import build_lifecycle_projection
except ModuleNotFoundError:
    from scripts.lifecycle_state import build_lifecycle_projection


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
START_DASH = "<!-- AUTO_STATE_SYNC_START -->"
END_DASH = "<!-- AUTO_STATE_SYNC_END -->"
START_ARCHIVE = "<!-- AUTO_ACCOUNT_FACT_SYNC_START -->"
END_ARCHIVE = "<!-- AUTO_ACCOUNT_FACT_SYNC_END -->"
ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def money(value) -> str:
    try:
        return f"{float(value):,.2f}元"
    except (TypeError, ValueError):
        return "—"


def display_name(position: dict) -> str:
    return f"{position.get('name', '')}（{position.get('code', '')}）"



def _structured_decision_from_report(event: dict) -> dict:
    """Recover Dashboard fields from the canonical frozen Formal Decision body.

    Direct Scheduled Review completion events carry the immutable user-facing
    body in presentation_binding.full_content. Missing structured fields stay
    explicit rather than being guessed from discovery or account state.
    """
    binding = event.get("presentation_binding") or {}
    content = str(binding.get("full_content") or event.get("final_content") or "")
    if not content:
        return {}

    def match(patterns: tuple[str, ...], source: str = content) -> str:
        for pattern in patterns:
            found = re.search(pattern, source, flags=re.MULTILINE)
            if found:
                return found.group(1).strip()
        return ""

    def section(name: str) -> str:
        marker = f"【{name}】"
        start = content.find(marker)
        if start < 0:
            return ""
        start += len(marker)
        next_heading = re.search(r"\n【[^】]+】", content[start:])
        return content[start:start + next_heading.start()] if next_heading else content[start:]

    risk = match((
        r"^\s*[-*]?\s*风险许可[：:]\s*([^。；\n]+)",
        r"风险许可为([^。；\n]+)",
    ))
    if not risk:
        denied = []
        if re.search(r"Trial\s*不授予", content, flags=re.IGNORECASE):
            denied.append("Trial不授予")
        if re.search(r"Confirm\s*不授予", content, flags=re.IGNORECASE):
            denied.append("Confirm不授予")
        if denied:
            risk = "未单独字段化（正文明确：" + "、".join(denied) + "）"

    candidate = match((
        r"^\s*[-*]?\s*唯一主候选[：:]\s*([^。；\n]+)",
        r"候选中仅将([^，。；\n]+)",
        r"机会ETF(?:是|为)[：:]?\s*([^，。；\n]+)",
    ))
    competition = section("全市场机会与资本竞争")
    if not candidate and competition and (
        "没有合格替代机会胜出现金" in competition
        or "没有新的主候选" in competition
    ):
        candidate = "无新的主候选；现金优先"

    amount = match((r"建议新增部署\s*([^，。；\n]+)",))
    release = match((r"实际释放\s*([^，。；\n]+)",))
    amount_action = "；".join(part for part in (
        f"新增部署{amount}" if amount else "",
        f"实际释放{release}" if release else "",
    ) if part)

    reason = match((
        r"^\s*[-*]?\s*(?:最大风险或0元主因|0元主因|决定性原因)[：:]\s*([^。\n]+)",
        r"主因是([^。\n]+)",
        r"原因是([^。\n]+)",
    ))
    if not reason and competition:
        sentences = re.split(r"(?<=[。！？])", competition.strip())
        reason = next((
            sentence.strip().rstrip("。！？")
            for sentence in sentences
            if "现金" in sentence and (
                "胜出" in sentence or "优势" in sentence or "优于" in sentence
            )
        ), "")

    lifecycle = []
    holdings = section("全部实际持仓")
    for line in holdings.splitlines():
        found = re.match(r"\s*(?:[-*]\s*)?(.+?)（(\d{6})）：([^。\n]+)", line)
        if found:
            name, code, action = (part.strip() for part in found.groups())
            lifecycle.append(f"{name}（{code}）：{action}")

    data_as_of = match((
        r"^\s*[-*]?\s*决策数据时点[：:]\s*([^。；\n]+)",
        r"^\s*[-*]?\s*判断时点[：:]\s*([^。；\n]+)",
    ))
    return {
        "risk_permission": risk,
        "main_candidate": candidate,
        "amount_action": amount_action,
        "decisive_reason": reason,
        "lifecycle": "；".join(lifecycle),
        "data_as_of_beijing": data_as_of or event.get("reviewed_at_beijing") or event.get("market_date") or "",
    }



def _expected_same_day_close_account_update(decision: dict, decision_stamp, account_stamp, account: dict) -> bool:
    """A close account snapshot after an intraday decision is expected, not stale.

    Formal Decision has a 14:30 node while the broker close fact is published at
    15:00.  The later close snapshot must not make the earlier intraday decision
    look invalid merely because it was produced later.  Real account changes
    remain stale; this exception is limited to the same market date and the
    configured 15:00 close fact.
    """
    if not decision_stamp or not account_stamp:
        return False
    market_date = str(decision.get("_event_market_date") or decision.get("market_date") or "").strip()
    if not market_date or decision_stamp.astimezone(SHANGHAI).date().isoformat() != market_date:
        return False
    if account_stamp.astimezone(SHANGHAI).date().isoformat() != market_date:
        return False
    if decision_stamp.astimezone(SHANGHAI).time().isoformat(timespec="minutes") >= "15:00":
        return False
    if account_stamp.astimezone(SHANGHAI).time().isoformat(timespec="minutes") != "15:00":
        return False
    source = str(account.get("source") or "").upper()
    return "CLOSE" in source or "收盘" in source or str(account.get("capture_mode") or "").upper() == "CLOSE"

def latest_canonical_formal_decision(root: Path = ROOT) -> dict:
    """Return the newest canonical decision/report, explicitly marked when stale.

    Formal Decision's canonical completed report is persisted under
    ``events/reviews/formal_*.json``.  Older decision facts remain under
    ``events/decisions``.  The Dashboard must select across both existing
    canonical representations; otherwise a newer completed report is invisible
    and an older decision is projected as the latest one.
    """
    candidates = []
    paths = list((root / "events" / "decisions").glob("*.json"))
    paths += list((root / "events" / "reviews").glob("formal_*.json"))
    paths += list((root / "requests" / "live_snapshot").glob("*__scheduled_review_completion.json"))
    for path in paths:
        try:
            event = load_json(path)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        event_type = str(event.get("event_type") or "").upper()
        if event_type not in {"FORMAL_DECISION", "FORMAL_DECISION_REPORT"}:
            continue
        decision = event.get("formal_decision") or event.get("decision") or {}
        if event_type == "FORMAL_DECISION_REPORT" and not decision:
            decision = event.get("decision_response") or {}
            if not decision:
                decision = _structured_decision_from_report(event)
        if not isinstance(decision, dict):
            continue
        if event_type == "FORMAL_DECISION_REPORT" and isinstance(decision.get("answers"), dict):
            answers = decision["answers"]
            risk = answers.get("RISK_PERMISSION") or {}
            candidate = answers.get("MAIN_CANDIDATE") or {}
            capital = answers.get("NEXT_UNIT_CAPITAL_USE") or {}
            lifecycle = []
            for key, value in answers.items():
                if not str(key).startswith("HOLDING:"):
                    continue
                code = str(key).split(":", 1)[1]
                action = value.get("final_action") if isinstance(value, dict) else value
                lifecycle.append(f"{code}：{action}")
            decision = {
                "risk_permission": risk.get("final_action"),
                "main_candidate": candidate.get("candidate_name") or candidate.get("final_action"),
                "capital_competition": {
                    "new_amount_yuan": capital.get("new_amount_yuan"),
                    "next_unit_capital_use": capital.get("final_action"),
                    "selected_state_reason": capital.get("selected_state_reason"),
                    "zero_amount_decisive_reason": capital.get("zero_amount_decisive_reason_if_zero"),
                },
                "lifecycle": "；".join(lifecycle),
                "data_as_of_beijing": event.get("reviewed_at_beijing") or event.get("market_date"),
            }
        stamp = event.get("decision_time_beijing") or event.get("decision_effective_at_beijing") or decision.get("decision_time") or decision.get("decision_effective_at_beijing") or event.get("reviewed_at_beijing") or event.get("recorded_at_beijing")
        try:
            from datetime import datetime
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=SHANGHAI)
        except (TypeError, ValueError):
            continue
        event_market_date = str(event.get("market_date") or event.get("market_date_beijing") or "").strip()
        if not event_market_date:
            completion_path = str(event.get("source_completion_path") or "").strip()
            if completion_path:
                try:
                    completion = load_json(root / completion_path)
                    event_market_date = str(
                        completion.get("market_date")
                        or completion.get("market_date_beijing")
                        or ""
                    ).strip()
                except (OSError, json.JSONDecodeError, TypeError):
                    event_market_date = ""
        if not event_market_date:
            event_market_date = str(parsed.astimezone(SHANGHAI).date())
        candidates.append((parsed, str(path), decision, event_market_date))
    if not candidates:
        return {}
    parsed, event_path, decision, event_market_date = max(
        candidates, key=lambda item: (item[0], item[1])
    )
    decision = dict(decision)
    decision["_event_market_date"] = event_market_date
    decision["_decision_path"] = event_path
    decision["_decision_stamp"] = parsed.isoformat()

    # A past execution remains historical when later confirmed account facts
    # invalidate its lifecycle. This is a validity guard, not a new state source.
    try:
        account = load_json(root / "data" / "state" / "account_fact.json")
    except (OSError, json.JSONDecodeError, TypeError):
        account = {}
    current_codes = {str(p.get("code") or "").strip() for p in account.get("positions") or [] if str(p.get("code") or "").strip()}
    lifecycle = decision.get("lifecycle")
    lifecycle_text = json.dumps(lifecycle, ensure_ascii=False) if isinstance(lifecycle, (dict, list)) else str(lifecycle or "")
    lifecycle_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", lifecycle_text))
    account_time = str(account.get("updated_at") or "")
    stale_reasons = []
    if current_codes and lifecycle_codes and not lifecycle_codes.issubset(current_codes):
        stale_reasons.append("正式决策生命周期包含当前账户已退出标的")
    if account_time:
        try:
            account_stamp = datetime.fromisoformat(account_time.replace("Z", "+00:00"))
            if account_stamp.tzinfo is None:
                account_stamp = account_stamp.replace(tzinfo=SHANGHAI)
            if account_stamp > parsed and not _expected_same_day_close_account_update(decision, parsed, account_stamp, account):
                stale_reasons.append("正式决策早于最新账户事实；该账户变化尚未纳入本次正式判断")
        except (TypeError, ValueError):
            pass
    decision["_current_applicability"] = "HISTORICAL" if stale_reasons else "CURRENT"
    decision["_stale_reason"] = "；".join(dict.fromkeys(stale_reasons))
    lifecycle = decision.get("lifecycle")
    if isinstance(lifecycle, dict):
        lifecycle = "；".join(f"{key}：{value}" for key, value in lifecycle.items())
    competition = decision.get("capital_competition") if isinstance(decision.get("capital_competition"), dict) else {}
    amount = competition.get("new_amount_yuan")
    canonical_use = str(decision.get("next_unit_capital_use") or competition.get("next_unit_capital_use") or "").strip()
    if decision.get("amount_action") or decision.get("action"):
        amount_action = decision.get("amount_action") or decision.get("action")
    elif canonical_use:
        amount_text = ""
        if amount not in (None, ""):
            try:
                amount_text = f"；新增金额{float(amount):,.2f}元"
            except (TypeError, ValueError):
                amount_text = ""
        amount_action = canonical_use + amount_text
    else:
        amount_action = "未提供"
    decisive_reason = (
        decision.get("decisive_reason")
        or decision.get("zero_amount_decisive_reason")
        or competition.get("zero_amount_decisive_reason")
        or competition.get("selected_state_reason")
        or "未提供"
    )
    return {
        "risk_permission": decision.get("risk_permission") or "未提供",
        "lifecycle": lifecycle or "未提供",
        "main_candidate": decision.get("main_candidate") or decision.get("candidate") or "无新的主候选。",
        "amount_action": amount_action,
        "decisive_reason": decisive_reason,
        "data_as_of_beijing": decision.get("data_as_of_beijing") or decision.get("data_as_of") or "未提供",
        "_current_applicability": decision.get("_current_applicability", "CURRENT"),
        "_stale_reason": decision.get("_stale_reason", ""),
        "_decision_stamp": decision.get("_decision_stamp", ""),
        "_decision_path": decision.get("_decision_path", ""),
        "_event_market_date": decision.get("_event_market_date", ""),
    }


def _formal_decision_index_records(root: Path, market_date: str) -> list[dict]:
    """Read compact same-day Formal Decision records from canonical events."""
    records: list[dict] = []
    seen: set[str] = set()
    for directory in (root / "events" / "reviews", root / "events" / "decisions", root / "requests" / "live_snapshot"):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                event = load_json(path)
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if (
                path.parent.name == "live_snapshot"
                and path.name.endswith("__scheduled_review_completion.json")
                and str(event.get("task_id") or "").strip() == "ETF正式决策"
            ):
                event = dict(event)
                event["event_type"] = "FORMAL_DECISION_REPORT"
                event["decision_time_beijing"] = event.get("requested_at_beijing")
                event["recorded_at_beijing"] = event.get("requested_at_beijing")
                event["decision_id"] = event.get("decision_id") or event.get("request_id")
                event["final_content"] = event.get("final_content") or (event.get("presentation_binding") or {}).get("full_content")
            if str(event.get("event_type") or "").upper() not in {"FORMAL_DECISION", "FORMAL_DECISION_REPORT"}:
                continue
            stamp = event.get("decision_time_beijing") or event.get("decision_effective_at_beijing") or event.get("reviewed_at_beijing") or event.get("recorded_at_beijing")
            try:
                from datetime import datetime
                parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=SHANGHAI)
                parsed = parsed.astimezone(SHANGHAI)
            except (TypeError, ValueError):
                continue
            # The index is a decision-day view.  A Formal Decision may
            # consume the prior legal market-date fact (for example a 09:02
            # pre-open decision consuming the prior close), so event market_date
            # must not replace the Beijing decision date for same-day indexing.
            event_date = parsed.date().isoformat()
            if event_date != market_date:
                continue
            identity = str(event.get("decision_id") or event.get("report_id") or event.get("task_run_id") or event.get("source_completion_path") or event.get("content_hash") or path)
            if identity in seen:
                continue
            seen.add(identity)
            binding = event.get("presentation_binding") or {}
            content = str(binding.get("full_content") or event.get("final_content") or "")
            decision = event.get("formal_decision") or event.get("decision") or event.get("decision_response") or {}
            if not isinstance(decision, dict):
                decision = {}
            # Direct Scheduled Review completion events may intentionally carry
            # only immutable full_content. Reuse the existing report parser for
            # projection fields; do not create a second decision fact.
            if not decision and content:
                decision = _structured_decision_from_report(event)
            answers = decision.get("answers") if isinstance(decision.get("answers"), dict) else {}
            risk = decision.get("risk_permission") or (answers.get("RISK_PERMISSION") or {}).get("final_action")
            candidate = decision.get("main_candidate") or (answers.get("MAIN_CANDIDATE") or {}).get("candidate_name")
            capital = decision.get("capital_competition") if isinstance(decision.get("capital_competition"), dict) else {}
            amount = decision.get("amount_action") or decision.get("action") or capital.get("next_unit_capital_use") or (answers.get("NEXT_UNIT_CAPITAL_USE") or {}).get("final_action")
            new_amount = capital.get("new_amount_yuan")
            if new_amount in (None, ""):
                new_amount = (answers.get("NEXT_UNIT_CAPITAL_USE") or {}).get("new_amount_yuan")
            release = decision.get("release_action") or capital.get("release_action") or "未提供"
            if not candidate and content:
                found = re.search(r"(?:唯一主候选|重点候选|最接近可执行的候选)[：:]?\s*([^。；\n]+)", content)
                candidate = found.group(1).strip() if found else ""
            if not risk and content:
                risk = "Trial不授予" if re.search(r"Trial\s*不授予", content, re.I) else ""
                if not risk and re.search(r"Confirm\s*不授予", content, re.I):
                    risk = "Confirm不授予"
            if not amount:
                found = re.search(r"建议新增部署\s*([^，。；\n]+)", content)
                amount = "新增部署" + found.group(1).strip() if found else "未提供"
            if new_amount not in (None, ""):
                amount = f"{amount}；{float(new_amount):,.2f}元"
            judgment_kind = str(event.get("judgment_kind") or event.get("decision_status") or event.get("assessment_status") or "").strip()
            if not judgment_kind:
                judgment_kind = "超时判断" if "超时判断" in content else ("降级判断" if "降级判断" in content else "正式判断")
            records.append({"stamp": parsed, "judgment_kind": judgment_kind, "candidate": candidate or "未结构化摘要", "risk": risk or "未结构化", "amount": amount or "未提供", "release": release, "identity": identity})
    records.sort(key=lambda item: (item["stamp"], item["identity"]))
    for index, item in enumerate(records):
        item["status"] = "当前判断" if index == len(records) - 1 else "已被后续判断覆盖"
    return records


def render_same_day_formal_decision_index(root: Path, account: dict) -> str:
    current_path = root / "data/state/CURRENT.json"
    current = load_json(current_path) if current_path.exists() else {}
    # Use the current projection's decision calendar date first.  Account
    # facts can legitimately remain at the prior close while a new intraday or
    # pre-open Formal Decision is already complete.
    market_date = str(
        current.get("generated_at_beijing")
        or current.get("generated_at")
        or current.get("market_date")
        or account.get("updated_at")
        or ""
    )[:10]
    records = _formal_decision_index_records(root, market_date) if market_date else []
    lines = ["### 当日正式决策索引", ""]
    if not records:
        return "\n".join(lines + ["- 当日尚未形成可回读的正式决策记录。"])
    lines += [
        "仅列当日【ETF正式决策】的紧凑摘要；正式判断、降级判断和超时判断均保留，重试不重复计数。",
        "",
        "|时间|类型|主要对象/动作|Trial/Confirm|金额/释放|当日成交关联|状态|",
        "|-|-|-|-|-|-|-|",
    ]
    trades = account.get("trades") or []
    canonical_trade_events = []
    for event_path in sorted((root / "events/trades").glob("*.json")):
        try:
            event = load_json(event_path)
        except Exception:
            continue
        attribution = event.get("execution_attribution") or {}
        if str(attribution.get("decision_id") or event.get("linked_decision_id") or ""):
            canonical_trade_events.append(event)
    for item in records:
        linked = [trade for trade in trades if str(trade.get("decision_id") or trade.get("linked_decision_id") or "") == item["identity"]]
        decision_stamp = item["stamp"].strftime("%Y-%m-%d %H:%M")
        linked += [
            trade for trade in trades
            if any(decision_stamp in str(label) for label in (trade.get("linked_formal_decisions") or []))
        ]
        linked += [event for event in canonical_trade_events if str((event.get("execution_attribution") or {}).get("decision_id") or event.get("linked_decision_id") or "") == item["identity"] and str(event.get("execution_date") or event.get("confirmed_at_beijing") or "")[:10] == market_date]
        decision_text = f"{item['candidate']} {item['release']}"
        same_day_object = [
            trade for trade in trades
            if str(trade.get("trade_time") or "")[:10] == market_date
            and str(trade.get("security_code") or trade.get("code") or "")
            and str(trade.get("security_code") or trade.get("code") or "") in decision_text
        ]
        if linked:
            execution = "已关联成交"
        elif same_day_object:
            execution = "同日成交、对象匹配；归因未确认"
        else:
            execution = "未确认"
        lines.append(f"|{item['stamp'].strftime('%H:%M')}|{item['judgment_kind']}|{item['candidate']}|{item['risk']}|{item['amount']}；释放{item['release']}|{execution}|{item['status']}|")
    return "\n".join(lines)


def render_lifecycle_projection(root: Path, account: dict) -> str:
    """Render the read-only lifecycle projection consumed by the Dashboard."""
    projection = build_lifecycle_projection(root, str(account.get("updated_at") or "")[:10] or None)
    items = projection.get("active_lifecycles") or []
    active_trials = [item for item in items if item.get("lifecycle_status") == "ACTIVE_TRIAL"]
    lines = ["### 当前有效生命周期", "", "仅展示由正式决策与真实成交事实共同确认的只读生命周期投影；不产生交易权限或订单。", ""]
    lines += [
        f"- 有效Trial：{len(active_trials)}个",
        f"- 到期/逾期待正式决议：{sum(1 for item in active_trials if item.get('decision_due') or item.get('overdue'))}个",
        "- 有效Confirm：当前投影未发现由正式决策与真实成交共同确认的有效Confirm。",
    ]
    lines += ["", "|对象|生命周期|状态|形成日期|当前T+|下一节点|状态说明|来源决策|", "|-|-|-|-:|-:|-|-|-|"]
    if active_trials:
        for item in active_trials:
            next_node = (
                "已结案"
                if item.get("lifecycle_status") == "RESOLVED"
                else f"形成正式决议（截止{item.get('mandatory_decision_market_date') or '未提供'}）"
            )
            explanation = item.get("lifecycle_status_explanation") or "生命周期解释尚未形成。"
            lines.append(
                f"|{item.get('security_name') or item.get('security_code')}（{item.get('security_code')}）|Trial|"
                f"{'逾期' if item.get('overdue') else ('待决议' if item.get('decision_due') else '有效')}|"
                f"{item.get('start_market_date') or '未提供'}|T+{item.get('current_t_plus') or 0}|"
                f"{next_node}|{explanation}|{item.get('source_decision_id') or '未提供'}|"
            )
    else:
        lines.append("|无|—|当前没有有效Trial|—|—|—|—|")
    lines += ["", "当前持仓的事实数量以上方‘当前持仓事实’为准；未被生命周期投影单独列出的持仓，不得据此推断为Trial或Confirm。"]
    return "\n".join(lines)


def render_decision_projection(decision: dict) -> str:
    """Render current or historical decision without presenting stale facts as current."""
    applicability = str(decision.get("_current_applicability") or "CURRENT")
    lines = ["### 最近一次正式决策", ""]
    if applicability != "CURRENT":
        lines.extend([
            "- 当前有效性：历史正式决策（不作为当前权限或当前持仓状态）",
            f"- 历史原因：{decision.get('_stale_reason') or '已被更新账户或成交事实覆盖'}",
        ])
    lines.extend([
        f"- 风险许可：{decision.get('risk_permission', '未提供')}",
        f"- 生命周期：{decision.get('lifecycle', '未提供')}",
        f"- 唯一主候选：{decision.get('main_candidate', '无新的主候选。')}",
        f"- 金额与动作：{decision.get('amount_action', '未提供')}",
        f"- 最大风险或0元主因：{decision.get('decisive_reason', '未提供')}",
        f"- 决策数据时点：{decision.get('data_as_of_beijing', '未提供')}",
    ])
    return "\n".join(lines)


def format_position_pnl(position: dict) -> str:
    """Exact broker P&L, explicit derived estimate, or unknown; never zero fallback."""
    def num(value):
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
    exact = next((position.get(k) for k in ("pnl", "holding_pnl") if position.get(k) not in (None, "")), None)
    exact_pct = next((position.get(k) for k in ("pnl_pct", "holding_pnl_pct") if position.get(k) not in (None, "")), None)
    estimated = False
    if exact is None:
        market_value, cost, quantity = (num(position.get(k)) for k in ("market_value", "cost", "quantity"))
        if market_value is not None and cost is not None and quantity is not None:
            exact = market_value - cost * quantity
            basis = cost * quantity
            exact_pct = exact / basis * 100 if basis else None
            estimated = True
    if exact is None:
        return "—"
    suffix = "（估算）" if estimated else ""
    pct = f"（{float(exact_pct):+.2f}%）" if exact_pct is not None else ""
    return f"{float(exact):,.2f}元{suffix}{pct}"


def latest_formal_risk() -> float | None:
    # Historical Known-net reviews are PIT compatibility evidence only.
    return None


def canonical_risk(equity: dict, formal_override: float | None = None) -> float | None:
    # Pure parser remains independently testable; production callers explicitly
    # supply the newest formal review value when one exists.
    if formal_override is not None:
        return float(formal_override)
    summary = equity.get("summary") or {}
    as_of = str(equity.get("as_of_transaction_date") or summary.get("as_of_transaction_date") or equity.get("replay_cutoff") or "")
    if not as_of:
        return None
    try:
        return float(summary["current_strategy_return_pct_gross"])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def canonical_risk_identity(equity: dict) -> dict:
    return {
        "source": "data/state/etf_strategy_equity.json::summary.current_strategy_return_pct_gross",
        "replay_cutoff": str(equity.get("replay_cutoff") or ""),
        "trade_fact_count": (equity.get("summary") or {}).get("trade_fact_count"),
    }


def normalize_dashboard_projection(text: str, root: Path = ROOT, account: dict | None = None) -> str:
    """Keep Dashboard a current projection and repair legacy newline serialization."""
    replacement = "|ETF层当前结构|当前持仓与观察角色仅以上方‘云端实时状态（自动同步）’中的canonical account projection为准；本区块不再复制当前角色列表。|"
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    lines = text.splitlines()
    cleaned = []
    in_corrections = False
    for line in lines:
        if line == "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->":
            in_corrections = True
            cleaned.append(line)
            continue
        if line == "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->":
            in_corrections = False
            cleaned.append(line)
            continue
        if not in_corrections:
            cleaned.append(line)
    lines = cleaned
    # The same-day decision index is the sole decision view. Remove legacy
    # latest-decision blocks that may have been persisted before the renderer
    # was narrowed to the index; otherwise normalization would preserve them.
    for legacy_heading in ("### 最近一次正式决策", "### 最近一次正式盘中决策", "### 最近一次正式收盘复盘"):
        while legacy_heading in lines:
            start = lines.index(legacy_heading)
            end = len(lines)
            for index in range(start + 1, len(lines)):
                if lines[index].startswith("### ") or lines[index].startswith("## "):
                    end = index
                    break
            del lines[start:end]
    for index, line in enumerate(lines):
        if "|ETF层当前结构|" in line:
            lines[index] = replacement
            break
    lifecycle_heading = "### 当前有效生命周期"
    lifecycle_marker = "<!-- AUTO_STATE_SYNC_END -->"
    if lifecycle_heading in lines and lifecycle_marker in lines:
        lifecycle_start = lines.index(lifecycle_heading)
        lifecycle_end = lines.index(lifecycle_marker, lifecycle_start)
        canonical_lifecycle = render_lifecycle_projection(root, account or {})
        lines[lifecycle_start:lifecycle_end] = canonical_lifecycle.splitlines()
    heading = "### 下一关键节点"
    if heading in lines:
        start = lines.index(heading)
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].startswith("## ") and lines[index] != heading:
                end = index
                break
        today = str((account or {}).get("updated_at") or "")[:10]
        next_day = "待由交易日历确定"
        try:
            calendar = load_json(root / "config/market/a_share_trading_calendar_2026.json")
            closed = set(calendar.get("closed_dates") or [])
            from datetime import date, timedelta
            cursor = date.fromisoformat(today) if today else date.today()
            for _ in range(370):
                cursor += timedelta(days=1)
                candidate = cursor.isoformat()
                if cursor.weekday() < 5 and candidate not in closed:
                    next_day = candidate
                    break
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        lines[start:end] = [
            heading, "",
            f"- 下一可交易节点：A股下一交易日 {next_day}（如相关动作只能在A股执行）；优先复核当前ACTIVE_TRIAL与正式行情/账户事实。",
            "- 已关闭Trial仅在下一可执行节点独立评估降低风险或退出；本投影不生成订单。",
            "- 当前无待处理结算现金约束。",
        ]
    result = "\n".join(lines)
    # Keep the registered correction block present even when the input
    # projection had no historical correction rows.
    if "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->" not in result:
        result += "\n<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->\n<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"
    return result + ("\n" if text.endswith("\n") else "")


def preserve_decision_block(existing: str) -> str:
    if START_DASH not in existing or END_DASH not in existing:
        return ""
    block = existing[existing.index(START_DASH) + len(START_DASH):existing.index(END_DASH)]
    starts = [
        block.find("### 最近一次正式决策"),
        block.find("### 最近一次正式盘中决策"),
        block.find("### 最近一次正式收盘复盘"),
    ]
    start = min([x for x in starts if x >= 0], default=-1)
    if start < 0:
        return ""
    end = block.find("同步请求：", start)
    if end < 0:
        end = len(block)
    return block[start:end].strip()


def build_dashboard_block(account: dict, equity: dict, existing: str, root: Path = ROOT) -> str:
    positions = account.get("positions") or []
    membership = active_account_asset_codes(root, account)
    etfs = [p for p in positions if normalize_code(p.get("code")) in membership["etf"]]
    stocks = [p for p in positions if normalize_code(p.get("code")) in membership["stocks"]]
    others = [p for p in positions if normalize_code(p.get("code")) in membership.get("other", set())]
    total_asset = float(account.get("total_asset") or 0)
    exposure = float(account.get("stock_market_value") or 0) / total_asset * 100 if total_asset else 0
    risk = canonical_risk(equity, latest_formal_risk())
    risk_identity = canonical_risk_identity(equity)
    risk_cutoff = risk_identity["replay_cutoff"] or "UNKNOWN"
    risk_source = risk_identity["source"]
    summary = equity.get("summary") or {}
    pending_fee_trades = [
        t for t in (account.get("trades") or [])
        if str(t.get("fee_status", "")).upper() in {"PENDING", "PENDING_CONFIRMATION", "UNKNOWN"}
    ]
    pending = bool(summary.get("unknown_fee_flag", True) or pending_fee_trades)
    fee_fact = effective_confirmed_fee_fact(root, equity.get("trades") or [])
    known_fees = fee_fact["effective_confirmed_fee_sum"]
    overlay_fees = fee_fact["executed_event_confirmed_fee_sum"]
    confirmed_account_fees = sum(float(t.get("fee") or 0) for t in (account.get("trades") or []) if str(t.get("fee_status", "")).upper() == "CONFIRMED")
    lines = [
        "## 云端实时状态（自动同步）", "",
        f"> 账户事实更新时间：{account.get('updated_at', '')}  ",
        f"> 账户事实来源：{account.get('source', '')}  ",
        f"> ETF策略风险as-of：{risk_cutoff}  ",
        f"> ETF策略风险来源：{risk_source}  ",
        "> 场景：ACCOUNT_FACT_MAINTENANCE  ",
        "> 账户事实与ETF策略风险使用各自canonical provenance；本区块不推导交易权限或下单。",
        "", "|项目|最新事实|", "|-|-|",
        f"|总资产|{money(account.get('total_asset'))}|",
        f"|股票市值|{money(account.get('stock_market_value'))}|",
        f"|账面现金|{money(account.get('cash'))}|",
        f"|待结算锁定资金|{money(account.get('reserved_cash_for_settlement', 0))}|",
        f"|可部署现金|{money(account.get('deployable_cash'))}|",
        f"|账户持仓盈亏|{money(account.get('holding_pnl'))}|",
        f"|当日盈亏|{money(account.get('daily_pnl'))}（{float(account.get('daily_pnl_pct') or 0):+.2f}%）|",
        f"|账户总风险暴露率|约{exposure:.2f}%|",
        f"|ETF策略风险率|约{risk:.4f}%（Gross；as-of {risk_cutoff}）|" if risk is not None else "|ETF策略风险率|当前canonical策略权益状态缺失|",
        f"|累计已知ETF费用（有效事实）|{money(known_fees)}；已执行成交overlay {money(overlay_fees)}；待确认费用状态：{'存在' if pending else '无'}|",
        f"|账户事实内已确认费用记录合计|{money(confirmed_account_fees)}（仅统计account_fact中明确标记CONFIRMED的记录；不代表历史累计ETF费用）|",
        "", "### 当前持仓事实", "",
        "|标的|数量|成本|现价|市值|浮动盈亏|", "|-|-:|-:|-:|-:|-:|",
    ]
    for p in positions:
        lines.append(
            f"|{display_name(p)}|{int(p.get('quantity') or 0):,}|"
            f"{float(p.get('cost') or p.get('cost_price') or 0):.3f}|{position_metric(p, 'current_price', 'last_price'):.3f}|"
            f"{money(p.get('market_value'))}|{format_position_pnl(p)}|"
        )
    etf_codes = {str(item.get("code")): str(item.get("name") or item.get("code")) for item in (load_json(root / "config/market/etf_monitor_universe.json").get("objects") or []) if item.get("code")}
    held_codes = membership["etf"]
    observed = [f"{name}（{code}）" for code, name in etf_codes.items() if code not in held_codes]
    lines += [
        "", f"持仓ETF：{'、'.join(display_name(p) for p in etfs) or '无'}。",
        f"账户个股：{'、'.join(display_name(p) for p in stocks) or '无'}。",
        f"账户其他证券：{'、'.join(display_name(p) for p in others) or '无'}。",
        f"观察ETF：{'、'.join(observed) or '无'}。",
        "观察ETF说明：仅展示经正式决策准入/保留、当前无持仓且仍值得跨节点持续监测的ETF；本节点池外发现对象不会因被发现而自动进入本列表。",
    ]
    # The same-day index is the sole Dashboard decision view.  It is rebuilt
    # from canonical events for the account market date, so prior-day entries
    # naturally roll off and no separate "latest decision" projection can
    # conflict with the index.
    lines += ["", render_same_day_formal_decision_index(root, account), "", render_lifecycle_projection(root, account)]
    return "\n".join(lines)


def update_experience(text: str, account: dict) -> tuple[str, int]:
    changed = 0
    for trade in account.get("trades") or []:
        if str(trade.get("fee_status", "")).upper() != "CONFIRMED":
            continue
        code = str(trade.get("code") or "")
        action = str(trade.get("action") or "")
        quantity = str(int(trade.get("quantity") or 0))
        price = str(trade.get("price") or "")
        date = str(trade.get("trade_time") or "")[:10]
        lines = text.splitlines()
        action_upper = action.upper()
        action_tokens = {action, "买入" if action_upper in {"BUY", "B"} else "", "卖出" if action_upper in {"SELL", "S"} else ""}
        normalized_quantity = quantity.replace(",", "")
        normalized_price = f"{float(trade.get('price')):.3f}" if trade.get('price') is not None else price
        for i, line in enumerate(lines):
            normalized_line = line.replace(",", "")
            action_match = any(token and token in line for token in action_tokens)
            price_match = price in line or normalized_price in line
            if not (line.startswith("|") and date in line and code in line and action_match and normalized_quantity in normalized_line and price_match):
                continue
            parts = line.split("|")
            if len(parts) < 10:
                continue
            fee = trade.get("fee")
            amount = trade.get("amount", trade.get("gross_amount"))
            parts[8] = f"{float(fee):.2f}" if fee is not None else parts[8]
            parts[9] = f"−{float(amount):,.2f}" if action.upper() in {"BUY", "买入"} else f"{float(amount):,.2f}"
            note = parts[10].strip()
            marker = "费用已确认" if str(fee) else "费用待确认"
            if marker not in note:
                note = (note + "；" if note else "") + marker
            parts[10] = note
            lines[i] = "|".join(parts)
            changed += 1
            break
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text, changed


def build_archive_fact_block(account: dict) -> str:
    trades = account.get("trades") or []
    trade_lines = []
    for trade in trades:
        if str(trade.get("fee_status", "")).upper() != "CONFIRMED":
            continue
        trade_lines.append(
            f"- {trade.get('object', trade.get('name', ''))}（{trade.get('code', '')}）"
            f"：{trade.get('action', '')}{int(trade.get('quantity') or 0)}份，"
            f"成交价{trade.get('price')}，成交本金{money(trade.get('gross_amount'))}，"
            f"已确认费用{money(trade.get('fee'))}，资金发生额{money(trade.get('amount'))}。"
        )
    if not trade_lines:
        trade_lines.append("- 本次账户事实未新增已确认费用的成交记录。")
    return "\n".join([
        "### 账户事实增量维护（仅客观事实）",
        f"- 账户事实确认时间：{account.get('updated_at', '')}",
        f"- 来源：{account.get('source', '')}",
        f"- 总资产：{money(account.get('total_asset'))}；可用资金：{money(account.get('cash'))}；持仓数量沿用最新确认账户事实。",
        *trade_lines,
        "- 本区块不生成交易权限、买卖建议或MASTER修改；未确认费用不写入已知费用。",
    ])


def sync_current_account_reference(root: Path, account: dict) -> bool:
    path = root / "data/state/CURRENT.json"
    if not path.exists():
        return False
    current = load_json(path)
    changed = False
    wanted = {
        "account_fact_path": "data/state/account_fact.json",
        "account_fact_updated_at": account.get("updated_at"),
        "account_fact_status": account.get("status"),
        "account_fact_source": account.get("source"),
    }
    for key, value in wanted.items():
        if current.get(key) != value:
            current[key] = value
            changed = True
    if changed:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def sync_formal_files(root: Path = ROOT, account: dict | None = None) -> dict:
    account = account or load_json(root / "data/state/account_fact.json")
    dashboard_result = sync_dashboard_projection(root, account)
    dash_changed = dashboard_result["dashboard_changed"]
    equity_path = root / "data/state/etf_strategy_equity.json"
    equity = load_json(equity_path) if equity_path.exists() else {"summary": {}}
    archive_path = root / "ETF市场行情档案_2026.md"
    experience_path = root / "ETF交易复盘与经验库_2026.md"

    existing_archive = archive_path.read_text(encoding="utf-8")
    new_archive = replace_block(existing_archive, START_ARCHIVE, END_ARCHIVE, build_archive_fact_block(account), before_heading="## 6. 历史Excel与专项数据来源")
    archive_changed = new_archive != existing_archive
    if archive_changed:
        write_formal_text_if_changed(root, archive_path.name, new_archive)

    existing_experience = experience_path.read_text(encoding="utf-8")
    new_experience, experience_updates = update_experience(existing_experience, account)
    experience_changed = new_experience != existing_experience
    if experience_changed:
        write_formal_text_if_changed(root, experience_path.name, new_experience)

    current_changed = sync_current_account_reference(root, account)
    research_archive_changed = archive_completed_research(root)
    return {
        "dashboard_changed": dash_changed,
        "archive_changed": archive_changed,
        "experience_changed": experience_changed,
        "experience_fee_rows_updated": experience_updates,
        "current_account_reference_changed": current_changed,
        "completed_research_archive_changed": research_archive_changed,
    }


def sync_dashboard_projection(root: Path = ROOT, account: dict | None = None) -> dict:
    """Maintain factual Dashboard projection without creating business actions."""
    account = account or load_json(root / "data/state/account_fact.json")
    equity_path = root / "data/state/etf_strategy_equity.json"
    equity = load_json(equity_path) if equity_path.exists() else {"summary": {}}
    dash_path = root / "ETF当前状态_DASHBOARD.md"
    existing_dash = dash_path.read_text(encoding="utf-8")
    new_dash = replace_block(existing_dash, START_DASH, END_DASH, build_dashboard_block(account, equity, existing_dash, root), after_heading=True)
    new_dash = normalize_dashboard_projection(new_dash, root, account)
    changed = new_dash != existing_dash
    if changed:
        write_formal_text_if_changed(root, dash_path.name, new_dash)
    return {"dashboard_changed": changed, "projection_scope": "FACTUAL_STATE_ONLY", "trade_authority": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result = sync_formal_files(root)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

