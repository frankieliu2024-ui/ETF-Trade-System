from __future__ import annotations

from typing import Any

from notification_center import STATE, read_json
from market_notification_common import number, pct, pct_change, range_position

ACCOUNT_FACT = STATE / "account_fact.json"
SNAPSHOTS = STATE.parent / "market" / "snapshots"


def _account_context() -> dict:
    data = read_json(ACCOUNT_FACT, {})
    positions = data.get("positions") or []
    held_etfs = {str(x.get("code") or ""): x for x in positions if str(x.get("asset_type") or "").upper() == "ETF"}
    held_stocks = {str(x.get("code") or ""): x for x in positions if str(x.get("asset_type") or "").upper() == "STOCK"}
    return {"data": data, "held_etfs": held_etfs, "held_stocks": held_stocks, "formal": data.get("formal_action") or {}}


def _formal_detail(formal: dict) -> str:
    lifecycle = str(formal.get("lifecycle") or "").strip()
    if "：" in lifecycle:
        lifecycle = lifecycle.rsplit("：", 1)[-1].strip()
    elif ":" in lifecycle:
        lifecycle = lifecycle.rsplit(":", 1)[-1].strip()
    status = str(formal.get("execution_status") or "").strip().upper()
    if status == "EXECUTED":
        status_text = "已执行"
    elif status in {"PENDING", "PENDING_EXECUTION"}:
        status_text = "待执行"
    elif status:
        status_text = "状态已记录"
    else:
        status_text = ""
    return "，".join(x for x in (lifecycle, status_text) if x) or "当前正式动作对象"


def _label(row: dict) -> str:
    name = str(row.get("provider_name") or row.get("name") or row.get("symbol") or row.get("code") or "对象")
    code = str(row.get("symbol") or row.get("code") or "")
    return f"{name}（{code}）" if code and code not in name else name


def _latest_intraday_row(code: str) -> tuple[str, dict] | None:
    """Return the latest same-day valid pre-close snapshot row for one ETF.

    This is descriptive evidence only. It does not create a lifecycle or trading
    decision; it lets the fixed close summary state what changed since the most
    recent valid intraday machine node instead of only reporting a close rank.
    """
    current = read_json(STATE / "CURRENT.json", {})
    market_date = str(current.get("market_date") or "")
    if not market_date or not SNAPSHOTS.exists():
        return None
    candidates: list[tuple[str, dict]] = []
    for path in SNAPSHOTS.glob(f"{market_date}_*.json"):
        snapshot = read_json(path, {})
        if str(snapshot.get("node") or "").lower() == "close":
            continue
        captured = str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "")
        if not captured:
            continue
        try:
            clock = captured.split("T", 1)[1][:5]
        except IndexError:
            continue
        if clock >= "15:00":
            continue
        row = next(
            (
                x
                for x in (snapshot.get("rows") or [])
                if str(x.get("symbol") or "") == str(code)
                and x.get("asset_class") == "ETF"
                and x.get("quality_status") == "PASS"
                and number(x.get("change_pct")) is not None
            ),
            None,
        )
        if row is not None:
            candidates.append((captured, row))
    return max(candidates, key=lambda x: x[0]) if candidates else None


def object_role(code: str, asset_class: str = "") -> tuple[str, str]:
    ctx = _account_context()
    code = str(code or "")
    asset = str(asset_class or "").upper()
    formal = ctx["formal"]
    if code and str(formal.get("applicable_object") or "") == code and str(formal.get("validity") or "") == "ACTIVE":
        return "ACTIVE_FORMAL_OBJECT", _formal_detail(formal)
    if code in ctx["held_etfs"]:
        return "HELD_ETF", "当前持仓ETF"
    if code in ctx["held_stocks"]:
        return "ACCOUNT_STOCK", "当前账户个股"
    if asset == "ETF":
        return "MONITORED_ETF", "当前监测ETF"
    if asset in {"A_SHARE_INDEX", "INDEX"}:
        return "INDEX", "正式指数"
    return "OTHER", "监测对象"


def compact_path(row: dict, feature: dict | None = None, *, include_current: bool = False) -> str:
    label = _label(row)
    day = number(row.get("change_pct"))
    open_ = number(row.get("open"))
    high = number(row.get("high"))
    low = number(row.get("low"))
    close = number(row.get("close"))
    prev = number(row.get("prev_close"))
    if prev is None and close is not None and day is not None and day > -99.9:
        prev = close / (1 + day / 100)
    gap = pct_change(open_, prev)
    pos = range_position(close, high, low)
    from_open = pct_change(close, open_)
    recovery = number((feature or {}).get("recovery_from_path_low_pct")) if feature else pct_change(close, low)
    retreat = number((feature or {}).get("retreat_from_path_high_pct")) if feature else pct_change(close, high)

    if gap is not None and gap <= -0.5 and day is not None and day >= -0.1:
        phrase = f"低开{pct(gap)}后基本收复"
    elif gap is not None and gap >= 0.5 and day is not None and day <= 0.1:
        phrase = f"高开{pct(gap)}后明显回吐"
    elif from_open is not None and from_open <= -0.5 and pos is not None and pos <= 0.30:
        phrase = "开盘后继续走弱，目前接近日内低位"
    elif from_open is not None and from_open >= 0.5 and pos is not None and pos >= 0.70:
        phrase = "开盘后继续走强，目前接近日内高位"
    elif pos is not None and pos <= 0.25 and (recovery is None or recovery <= 0.20):
        phrase = "当前接近日内低位，尚未出现有效修复"
    elif pos is not None and pos >= 0.75 and (retreat is None or retreat >= -0.20):
        phrase = "当前接近日内高位，强势尚未明显回吐"
    else:
        phrase = "当前处于日内区间中部，方向尚未形成新的强化"
    return f"{label}{phrase}" + (f"（当前{pct(day)}）" if include_current else "")


def a_share_structure(indices: dict[str, dict], etfs: list[dict], features: dict[str, dict], *, is_close: bool = False) -> tuple[list[str], list[str], str, str]:
    sh = indices.get("000001") or {}
    star = indices.get("000688") or {}
    cyb = indices.get("399006") or {}
    sh_ret = number(sh.get("change_pct"))
    star_ret = number(star.get("change_pct"))
    cyb_ret = number(cyb.get("change_pct"))
    growth_vals = [x for x in (star_ret, cyb_ret) if x is not None]
    growth = sum(growth_vals) / len(growth_vals) if growth_vals else None

    if sh_ret is not None and growth is not None and sh_ret - growth >= 0.45:
        structure = "宽基相对稳定，科技成长明显偏弱"
    elif sh_ret is not None and growth is not None and growth - sh_ret >= 0.45:
        structure = "科技成长明显强于宽基，风险偏好向成长扩散"
    elif growth is not None and growth <= -0.6:
        structure = "科技成长整体偏弱"
    elif growth is not None and growth >= 0.6:
        structure = "科技成长整体偏强"
    else:
        structure = "指数整体震荡，风格分化有限"

    headline = [f"- **市场结构**：{structure}。"]
    index_values = []
    for code, row in (("000001", sh), ("000688", star), ("399006", cyb)):
        if row and number(row.get("change_pct")) is not None:
            index_values.append(f"{_label(row)}{pct(number(row.get('change_pct')))}")
    if index_values:
        headline.append("- **指数反馈**：" + "，".join(index_values) + "。")

    ctx = _account_context()
    held = ctx["held_etfs"]
    etf_map = {str(x.get("symbol") or ""): x for x in etfs}
    held_rows = [etf_map[c] for c in held if c in etf_map and number(etf_map[c].get("change_pct")) is not None]
    formal = ctx["formal"]
    formal_code = str(formal.get("applicable_object") or "") if str(formal.get("validity") or "") == "ACTIVE" else ""
    formal_row = etf_map.get(formal_code) if formal_code else None

    weakest = min(held_rows, key=lambda x: float(x.get("change_pct") or 0)) if held_rows else None
    strongest = max(held_rows, key=lambda x: float(x.get("change_pct") or 0)) if held_rows else None
    details = []
    if formal_row is not None and number(formal_row.get("change_pct")) is not None:
        formal_ret = number(formal_row.get("change_pct"))
        phase_word = "收盘" if is_close else "当前"
        formal_text = f"{phase_word}{_formal_detail(formal)}对象{_label(formal_row)}{phase_word}{pct(formal_ret)}"
        previous = _latest_intraday_row(formal_code)
        if previous is not None:
            previous_time, previous_row = previous
            previous_ret = number(previous_row.get("change_pct"))
            if formal_ret is not None and previous_ret is not None:
                formal_text += f"，较最近盘中有效节点（{previous_time[11:16]}）{formal_ret - previous_ret:+.2f}个百分点"
        if weakest is not None and str(weakest.get("symbol") or "") != formal_code:
            weak_ret = number(weakest.get("change_pct"))
            if formal_ret is not None and weak_ret is not None:
                spread = formal_ret - weak_ret
                if spread > 0:
                    formal_text += f"，仍强于{_label(weakest)}{spread:.2f}个百分点"
                elif spread < 0:
                    formal_text += f"，弱于{_label(weakest)}{abs(spread):.2f}个百分点"
        details.append(formal_text)
    if weakest is not None and str(weakest.get("symbol")) != formal_code:
        details.append(f"持仓中偏弱的是{_label(weakest)}{pct(number(weakest.get('change_pct')))}")
    # When an active Trial/Confirm/formal object exists, its own evolution and
    # closest relevant comparison outrank a generic cross-market "strongest"
    # holding. This avoids diluting the current hypothesis with ranking noise.
    if formal_row is None and strongest is not None and weakest is not None and str(strongest.get("symbol")) != str(weakest.get("symbol")):
        details.append(f"持仓中相对较强的是{_label(strongest)}{pct(number(strongest.get('change_pct')))}")
    if details:
        headline.append("- **ETF自身反馈**：" + "；".join(details) + "。")

    path_parts = []
    if star:
        path_parts.append(compact_path(star, features.get("000688")))
    if cyb:
        path_parts.append(compact_path(cyb, features.get("399006")))
    if sh:
        path_parts.append(compact_path(sh, features.get("000001")))
    if sh_ret is not None and growth is not None and sh_ret - growth >= 0.45:
        path_summary = "；".join(path_parts[:2]) + "；而" + path_parts[-1] + "。这更像科技成长局部降温，而不是全市场同步风险释放。"
    elif sh_ret is not None and growth is not None and growth - sh_ret >= 0.45:
        path_summary = "；".join(path_parts[:2]) + "；而" + path_parts[-1] + "。强势主要集中在科技成长方向，需要继续验证ETF层是否同步接受。"
    else:
        path_summary = "；".join(path_parts) + "。"
    path_lines = []
    if formal_row is not None:
        path_lines.append("- **当前正式对象路径**：" + compact_path(formal_row, features.get(formal_code), include_current=True) + "。")
    path_lines.append("- " + path_summary)

    focus = []
    if formal_row is not None:
        focus.append(f"优先复核{_label(formal_row)}既有{_formal_detail(formal)}假设是否继续成立")
    if weakest is not None:
        focus.append(f"比较{_label(weakest)}等持仓继续占用资本的边际效率")
    implication = (
        f"{structure}。" + ("；".join(focus) + "。" if focus else "需要重新比较持仓/观察ETF的相对强弱和资本效率。")
        + "跨市场ETF的相对涨幅只用于解释不同风险因子，不替代A股自身反馈，也不等同资本效率排名。"
    )
    if formal_row is not None:
        action = f"不因通知机械交易；优先复核{_label(formal_row)}当前{_formal_detail(formal)}假设"
        if weakest is not None and str(weakest.get("symbol")) != formal_code:
            action += f"，同时比较{_label(weakest)}的资本占用效率"
        action += "；只有正式机会、金额或卖出动作改变时执行。"
    else:
        action = "不因通知机械交易；先复核当前持仓/观察ETF的相对强弱和风险收益，只有正式机会、金额或卖出动作改变时执行。"
    return headline, path_lines, implication, action


def shock_implication(code: str, name: str, asset_class: str, market: str) -> tuple[str, str]:
    role, detail = object_role(code, asset_class)
    label = f"{name}（{code}）" if code and code not in name else name
    if market == "A_SHARE":
        if role == "ACTIVE_FORMAL_OBJECT":
            implication = f"{label}是当前正式动作/生命周期对象（{detail}）。本次异动直接压力测试既有假设；优先判断相对其他持仓/观察ETF的强弱是否失效，以及继续占用或新增资本是否仍有效率。"
            if "Trial" in detail:
                action = f"继续验证{label}当前Trial，不因单次价格波动机械卖出或追加；重点复核相对强弱、假设有效性和边际资本效率，只有正式机会、金额或卖出动作改变时执行。"
            else:
                action = f"继续按{label}当前生命周期验证，不因单次价格波动机械交易；只有正式机会、金额或卖出动作改变时执行。"
        elif role == "HELD_ETF":
            implication = f"{label}是当前持仓ETF。本次异动直接影响其持仓风险收益和资本占用效率，应与其他持仓/观察ETF重新比较，而不是只看绝对涨跌。"
            action = "先复核持仓假设和边际资本效率；没有正式卖出动作或独立新机会通过完整链时，不机械交易。"
        elif role == "MONITORED_ETF":
            implication = f"{label}是监测ETF。本次异动提高其注意力优先级，但只有相对强弱、风险收益和独立机会假设共同改善，才可能升级为主候选或Trial/Confirm机会。"
            action = "提高观察优先级但不直接交易；等待正式机会判断形成。"
        elif role == "ACCOUNT_STOCK":
            implication = f"{label}是账户个股。本次异动先影响其独立假设和资金释放价值，并可能通过产业链影响相关ETF；不能仅凭个股涨跌改动ETF仓位。"
            action = "复核个股独立假设、资金释放价值及产业传导；只有正式持仓或资本再配置判断改变时执行。"
        else:
            implication = f"{label}代表A股本地风险偏好或风格结构变化。先看变化是否扩散到当前持仓/观察ETF，再判断候选和资本效率是否真正改变。"
            action = "先验证ETF层是否接受该指数信号；不因指数单一波动机械交易。"
        return implication, action

    direct = "相关持仓/观察ETF"
    if code == "HSTECH":
        direct = "恒生科技ETF（513180）"
    elif code == "N225":
        direct = "日经ETF（513520）"
    elif code in {"NDX", "SOX", "QQQ", "SOXX"}:
        direct = "纳指ETF（159941）及A股科技风险篮子"
    implication = f"{label}属于海外/区域结构证据。先看A股本地价格是否接受、减弱或背离，再复核{direct}的自身反馈、当前主候选和边际资本效率；若海外与A股背离，以A股自身反馈为主。"
    action = "把该信号纳入最近A股正式节点复核，不因单一海外/区域对象机械调整A股仓位。"
    return implication, action