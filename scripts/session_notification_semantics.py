from __future__ import annotations

from market_notification_common import number, pct, pct_change, range_position


def _us_tone(tech: float | None, semi: float | None) -> str:
    vals = [x for x in (tech, semi) if x is not None]
    if len(vals) < 2:
        return "信息不足"
    spread = semi - tech
    if tech * semi < 0 and abs(spread) >= 0.75:
        return "明显分化"
    avg = sum(vals) / len(vals)
    if avg >= 1.5:
        return "明显偏强"
    if avg >= 0.5:
        return "偏强"
    if avg <= -1.5:
        return "明显偏弱"
    if avg <= -0.5:
        return "偏弱"
    return "相对平稳"


def _path_phrase(obj: dict, *, node: str) -> str:
    path = obj.get("regular_session_path") or {}
    pos = number(path.get("range_position"))
    first = number(path.get("first_hour_change_pct"))
    late = number(path.get("late_session_change_pct"))
    from_open = number(obj.get("regular_session_change_from_open_pct"))
    if node == "OPEN":
        if from_open is not None and from_open >= 0.5:
            return "开盘后继续走强"
        if from_open is not None and from_open <= -0.5:
            return "开盘后明显回吐"
        return "开盘后暂未形成新的强化"
    if pos is not None and pos >= 0.8 and late is not None and late > 0:
        if first is not None and first < 0:
            return "首小时承压后逐步修复，尾段继续走强并收在日内高位附近"
        return "全天维持强势，尾段继续走强并收在日内高位附近"
    if pos is not None and pos <= 0.2 and late is not None and late < 0:
        if first is not None and first > 0:
            return "首小时冲高后持续回落，尾段仍弱并收在日内低位附近"
        return "全天承压，尾段仍弱并收在日内低位附近"
    if first is not None and late is not None and first * late < 0:
        return "早段与尾段方向相反，日内结构存在明显修复或回吐"
    return "日内路径未形成比最终涨跌更强的新结论"


def _implication_lines(tone: str) -> str:
    if tone in {"明显偏强", "偏强"}:
        return (
            "- **外部含义**：美股科技风险偏好提供正向背景。\n"
            "- **本地验证**：下一A股节点先确认本地科技方向是否接受该外部信号。\n"
            "- **资本比较**：重点复核纳指ETF（159941）、通信/科创/半导体风险篮子的自身反馈与边际资本效率。"
        )
    if tone in {"明显偏弱", "偏弱"}:
        return (
            "- **外部含义**：美股科技风险偏好转弱，提高A股高相关科技篮子的风险复核优先级。\n"
            "- **本地验证**：先检查A股自身承接是否同步减弱。\n"
            "- **资本比较**：只有本地反馈也转弱，才进一步影响持仓风险收益与资本配置。"
        )
    if tone == "明显分化":
        return (
            "- **外部含义**：海外内部已不能用单一科技方向概括。\n"
            "- **本地验证**：下一A股节点分别验证宽科技与半导体链反馈。\n"
            "- **边界**：不得把一个海外指数的强弱直接外推到全部科技ETF。"
        )
    return (
        "- **外部含义**：海外科技结构尚不足以单独改变A股判断。\n"
        "- **本地验证**：作为下一A股节点背景证据保存。\n"
        "- **边界**：若海外与A股反馈背离，以A股自身反馈为主。"
    )


def us_session_structure(*, tech: dict, semi: dict, tech_label: str, semi_label: str, direct: bool, node: str) -> tuple[list[str], list[str], str, str, str]:
    tech_change = number(tech.get("regular_session_change_vs_previous_close_pct"))
    semi_change = number(semi.get("regular_session_change_vs_previous_close_pct"))
    tone = _us_tone(tech_change, semi_change)
    basis = "纳斯达克100指数（NDX）/费城半导体指数（SOX）直接指数" if direct else "直接指数不可用，降级为纳指100ETF代理（QQQ）/半导体ETF代理（SOXX）"
    spread = (semi_change - tech_change) if tech_change is not None and semi_change is not None else None

    if tone in {"明显偏强", "偏强"}:
        structure = "美股科技风险偏好偏强"
    elif tone in {"明显偏弱", "偏弱"}:
        structure = "美股科技风险偏好偏弱"
    elif tone == "明显分化":
        structure = "美股科技与半导体明显分化"
    else:
        structure = "美股科技结构相对平稳"

    headline = [f"- **外部结构**：{structure}。"]
    headline.append(f"- **宽科技**：{tech_label}{pct(tech_change)}。")
    headline.append(f"- **半导体**：{semi_label}{pct(semi_change)}。")
    if spread is not None and abs(spread) >= 0.75:
        headline.append(f"- **结构差**：两者相差约{abs(spread):.2f}个百分点。")
    headline.append(f"- **数据口径**：{basis}。")

    path_lines = [
        f"- **{tech_label}**：{_path_phrase(tech, node=node)}。",
        f"- **{semi_label}**：{_path_phrase(semi, node=node)}。",
    ]

    implication = _implication_lines(tone)
    action = "纳入下一A股正式节点复核：先看本地传导，再看相关ETF自身反馈和资本效率；不因海外单一信号机械交易。"
    return headline, path_lines, implication, action, tone
