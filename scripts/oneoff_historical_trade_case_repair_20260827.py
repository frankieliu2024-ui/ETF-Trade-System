from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP_PATH = ROOT / "ETF交易复盘与经验库_2026.md"
CHECK_PATH = ROOT / "scripts" / "check_system_consistency.py"


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing expected anchor: {old[:100]}")
    return text.replace(old, new, 1)


def patch_experience() -> None:
    text = EXP_PATH.read_text(encoding="utf-8")
    mapping = {
        "|2026-07-13 09:36:14|纳指ETF（159941）|159941|买入|6,200|1.611|9,988.20|5.00|−9,993.20|初始持仓|": "|2026-07-13 09:36:14|纳指ETF（159941）|159941|买入|6,200|1.611|9,988.20|5.00|−9,993.20|CASE-20260713-01 初始组合建立|",
        "|2026-07-13 09:36:28|科创50ETF（588000）|588000|买入|6,800|2.179|14,817.20|7.41|−14,824.61|初始持仓|": "|2026-07-13 09:36:28|科创50ETF（588000）|588000|买入|6,800|2.179|14,817.20|7.41|−14,824.61|CASE-20260713-01 初始组合建立|",
        "|2026-07-13 09:36:43|科创创业ETF（159781）|159781|买入|7,600|1.305|9,918.00|5.00|−9,923.00|初始持仓|": "|2026-07-13 09:36:43|科创创业ETF（159781）|159781|买入|7,600|1.305|9,918.00|5.00|−9,923.00|CASE-20260713-01 初始组合建立|",
        "|2026-07-13 09:37:04|半导体设备ETF（561980）|561980|买入|17,400|0.873|15,190.20|7.60|−15,197.80|初始持仓|": "|2026-07-13 09:37:04|半导体设备ETF（561980）|561980|买入|17,400|0.873|15,190.20|7.60|−15,197.80|CASE-20260713-01 初始组合建立|",
        "|2026-07-14 13:27:05|纳指ETF（159941）|159941|买入|3,100|1.607|4,981.70|5.00|−4,986.70|历史增仓|": "|2026-07-14 13:27:05|纳指ETF（159941）|159941|买入|3,100|1.607|4,981.70|5.00|−4,986.70|CASE-20260713-01 连续增仓|",
        "|2026-07-14 13:27:38|科创创业ETF（159781）|159781|买入|3,800|1.294|4,917.20|5.00|−4,922.20|历史增仓|": "|2026-07-14 13:27:38|科创创业ETF（159781）|159781|买入|3,800|1.294|4,917.20|5.00|−4,922.20|CASE-20260713-01 连续增仓|",
        "|2026-07-14 13:28:05|科创50ETF（588000）|588000|买入|2,300|2.116|4,866.80|5.00|−4,871.80|历史增仓|": "|2026-07-14 13:28:05|科创50ETF（588000）|588000|买入|2,300|2.116|4,866.80|5.00|−4,871.80|CASE-20260713-01 连续增仓|",
        "|2026-07-14 13:28:15|半导体设备ETF（561980）|561980|买入|6,300|0.837|5,273.10|5.00|−5,278.10|历史增仓|": "|2026-07-14 13:28:15|半导体设备ETF（561980）|561980|买入|6,300|0.837|5,273.10|5.00|−5,278.10|CASE-20260713-01 连续增仓|",
        "|2026-07-15 13:30:57|科创50ETF（588000）|588000|买入|2,400|2.066|4,958.40|5.00|−4,963.40|历史增仓|": "|2026-07-15 13:30:57|科创50ETF（588000）|588000|买入|2,400|2.066|4,958.40|5.00|−4,963.40|CASE-20260713-01 连续增仓|",
        "|2026-07-15 13:31:15|科创创业ETF（159781）|159781|买入|3,900|1.280|4,992.00|5.00|−4,997.00|历史增仓|": "|2026-07-15 13:31:15|科创创业ETF（159781）|159781|买入|3,900|1.280|4,992.00|5.00|−4,997.00|CASE-20260713-01 连续增仓|",
        "|2026-07-15 14:36:53|半导体设备ETF（561980）|561980|买入|6,300|0.787|4,958.10|5.00|−4,963.10|历史增仓|": "|2026-07-15 14:36:53|半导体设备ETF（561980）|561980|买入|6,300|0.787|4,958.10|5.00|−4,963.10|CASE-20260713-01 连续增仓|",
        "|2026-07-28 09:31:36|宁德时代（300750）|300750|买入|100|395.000|39,500.00|19.17|−39,519.17|IPO_BASE_STOCK|": "|2026-07-28 09:31:36|宁德时代（300750）|300750|买入|100|395.000|39,500.00|19.17|−39,519.17|CASE-20260728-01 IPO_BASE_STOCK建立|",
        "|2026-07-28 09:32:24|工业富联（601138）|601138|买入|600|60.010|36,006.00|17.84|−36,023.84|IPO_BASE_STOCK|": "|2026-07-28 09:32:24|工业富联（601138）|601138|买入|600|60.010|36,006.00|17.84|−36,023.84|CASE-20260728-01 IPO_BASE_STOCK建立|",
        "|2026-08-27 10:08:43|通信ETF（515880）|515880|买入|7,400|0.671|4,965.40|待确认|−4,965.40（未含待确认费用）|2026-08-27 Trial；关联决策20260827_095649_515880_trial_5000；真实成交已执行|": "|2026-08-27 10:08:43|通信ETF（515880）|515880|买入|7,400|0.671|4,965.40|待确认|−4,965.40（未含待确认费用）|CASE-20260827-01 Trial；关联决策20260827_095649_515880_trial_5000；真实成交已执行|",
    }
    for old, new in mapping.items():
        text = replace_once(text, old, new)

    case_anchor = "### 2.13 CASE系统贡献索引"
    cases = """### 2.13 CASE-20260713-01：初始ETF组合建立与连续增仓（历史基线补录）

- 背景／生命周期：2026-07-13至07-15共完成11笔ETF买入，构成当前ETF策略最早可核对的初始组合与连续增仓基线。该阶段早于现行V2.2.18生命周期和风险许可体系，专项审计只建立成交归属，不把后来的Trial、Confirm或风险规则倒填为当时决策。
- 执行：纳指ETF（159941）累计买入9,300份；科创50ETF（588000）累计买入11,500份；科创创业ETF（159781）累计买入15,300份；半导体设备ETF（561980）累计买入30,000份。11笔成交本金合计84,860.90元，已确认费用60.01元，资金流出合计84,920.91元。
- 关键证据边界：当前能够完整核对的是中国银河证券对账单中的成交时间、方向、数量、价格、费用与资金发生额；缺少足以按现行规则重建当时每一笔事前机会判断、风险许可和候选比较的Point-in-Time证据，因此不伪造历史Trial／Confirm结论。
- 结果：这些成交形成后续所有ETF持仓成本、风险暴露和生命周期CASE的历史起点；7月16日以后出现的独立Confirm、Trial、降低风险与退出仍分别归入各自后续CASE，不与本基线CASE混写。
- 判断质量：不作事后评分。没有足够当时可见证据证明或否定初始配置质量，不能使用7月16日以后行情回推7月13日至15日必然应买、少买或不买。
- 执行质量：事实层面完成。成交与费用已由对账单核对；是否存在更优盘中成交位置没有分钟级Point-in-Time证据，不做事后最优执行比较。
- 风险收益质量／资本使用效率：仅作为历史起点保留。后续风险集中、共同风险和资本效率问题由当时已经形成的CASE与OBS评价，不反向把后续规则缺陷归罪于基线成交。
- 最终结果／反事实：本CASE是历史成交归属补录，不是重新制造历史交易决策。唯一允许的结论是11笔真实成交属于同一初始组合建立阶段；不构造“如果当时按现行MASTER执行”的伪历史。
- 系统贡献：补齐历史来源链，使最早11笔ETF成交具有唯一CASE归属，并明确Point-in-Time边界。

### 2.14 CASE-20260728-01：打新底仓建立（历史基线补录）

- 背景／生命周期：2026-07-28账户建立两只打新底仓：宁德时代（300750）100股、工业富联（601138）600股。该动作属于账户资产角色建立，不属于ETF Trial／Confirm机会，也不因后续系统把打新底仓纳入统一资本比较而倒填新的历史交易许可。
- 执行：宁德时代（300750）09:31:36以395.000元买入100股，成交本金39,500.00元、费用19.17元；工业富联（601138）09:32:24以60.010元买入600股，成交本金36,006.00元、费用17.84元。两笔成交本金合计75,506.00元，费用37.01元，资金流出75,543.01元。
- 关键证据边界：当前可核对事实来自中国银河证券对账单和后续账户事实；没有充分Point-in-Time材料支持用现行ETF机会框架评价两笔底仓建立是否为当时最优资本用途。
- 结果：两只个股随后作为账户确认的IPO_BASE_STOCK参与资本统一比较；工业富联（601138）于2026-08-13卖出100股的资金释放另归CASE-20260813-01，剩余底仓继续按账户事实管理。
- 判断质量／执行质量／风险收益质量：不作后见之明评分。底仓建立事实已核对，后续价格表现不反向证明买入正确或错误。
- 资本使用效率：从后续系统视角只作为可释放资本来源参与比较，不把7月28日建立动作机械解释为长期必须持有。
- 最终结果／反事实：本CASE只补齐两笔真实成交的历史归属，不构造当时不存在的ETF生命周期标签或后续规则。
- 系统贡献：补齐两笔打新底仓建立成交的CASE来源链，并把“资产角色建立”与“ETF机会决策”明确分开。

### 2.15 CASE系统贡献索引"""
    text = replace_once(text, case_anchor, cases)

    contrib_anchor = "|CASE-20260716-01|验证已有规则|验证Confirm证据与失效后退出纪律|"
    contrib = "|CASE-20260713-01|无新增规则价值|历史基线补录；补齐最早11笔ETF成交唯一CASE归属并明确Point-in-Time边界|\n|CASE-20260728-01|无新增规则价值|历史基线补录；补齐两笔打新底仓建立成交归属并区分账户角色与ETF机会决策|\n"
    text = replace_once(text, contrib_anchor, contrib + contrib_anchor)

    maint_anchor = "|2026-08-27|通信ETF（515880）Trial CASE与正式成交闭环维护|新增CASE-20260827-01并将自动待复盘入口升级为正式CASE关联；同步建立README防版本漂移和CASE完整性一致性门禁，不修改交易规则、金额档或自动交易权限|"
    maint_row = "|2026-08-27|午间历史成交→CASE专项完整审计|核对2026-07-13以来27笔证券交易，发现14笔成交索引缺少显式CASE归属；新增CASE-20260713-01与CASE-20260728-01两个历史基线CASE，并把通信ETF（515880）8月27日成交索引显式归入CASE-20260827-01；完成27/27唯一CASE映射，不使用后续规则伪造历史决策|"
    text = replace_once(text, maint_anchor, maint_anchor + "\n" + maint_row)
    EXP_PATH.write_text(text, encoding="utf-8")


def patch_consistency() -> None:
    text = CHECK_PATH.read_text(encoding="utf-8")
    if "def _validate_historical_trade_case_mapping" not in text:
        anchor = "\ndef _validate_readme_front_door(report: dict) -> None:\n"
        func = r'''
def _validate_historical_trade_case_mapping(report: dict) -> None:
    """Require every canonical securities trade-index row to have exactly one valid CASE owner."""
    import re

    experience = (ROOT / "ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
    start_token = "### 2.1 2026-07-13以来完整证券成交索引"
    end_token = "### 2.2 银证转账与非交易现金流水"
    errors = []
    rows = []
    try:
        start = experience.index(start_token)
        section = experience[start:experience.index(end_token, start)]
    except ValueError:
        section = ""
        errors.append("transaction_index_section_missing")
    for line in section.splitlines():
        if not line.startswith("|2026-"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 10:
            errors.append("malformed_row=" + line[:80])
            continue
        rows.append(cols)
    headings = set(re.findall(r"^###\s+.*?(CASE-\d{8}-\d{2})[:：]", experience, re.MULTILINE))
    etf_count = 0
    stock_count = 0
    for cols in rows:
        dt, name, code, side, qty, price, principal, fee, cashflow, remark = cols[:10]
        case_ids = sorted(set(re.findall(r"CASE-\d{8}-\d{2}", remark)))
        if len(case_ids) != 1:
            errors.append(f"{dt}:{code}:case_count={len(case_ids)}")
        elif case_ids[0] not in headings:
            errors.append(f"{dt}:{code}:missing_case_heading={case_ids[0]}")
        if "ETF" in name:
            etf_count += 1
        else:
            stock_count += 1
    declared = re.search(r"共(\d+)笔证券交易：ETF\s*(\d+)笔、个股(\d+)笔", section)
    if declared:
        declared_counts = tuple(map(int, declared.groups()))
        actual_counts = (len(rows), etf_count, stock_count)
        if declared_counts != actual_counts:
            errors.append(f"declared_counts={declared_counts} actual={actual_counts}")
    else:
        errors.append("declared_trade_counts_missing")
    status = "FAIL" if errors else "PASS"
    report.setdefault("checks", []).append({
        "name": "formal_files:historical_trade_case_mapping",
        "status": status,
        "detail": f"trade_rows={len(rows)} etf={etf_count} stock={stock_count} missing={errors}",
    })
    for item in errors:
        message = "historical_trade_case_mapping:" + item
        if message not in report.setdefault("errors", []):
            report["errors"].append(message)
    report["hard_error_count"] = len(report.get("errors") or [])
    report["warning_count"] = len(report.get("warnings") or [])
    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")

'''
        text = replace_once(text, anchor, "\n" + func + "def _validate_readme_front_door(report: dict) -> None:\n")
    call_anchor = "    _validate_trade_event_formal_sync(report)\n    _validate_readme_front_door(report)\n"
    if "    _validate_historical_trade_case_mapping(report)\n" not in text:
        text = replace_once(text, call_anchor, "    _validate_trade_event_formal_sync(report)\n    _validate_historical_trade_case_mapping(report)\n    _validate_readme_front_door(report)\n")
    print_anchor = '        "formal_trade_event_sync": next((x for x in report.get("checks", []) if x.get("name") == "formal_files:executed_trade_event_sync"), {}),\n'
    if "historical_trade_case_mapping" not in text[text.find("def main"):]:
        if print_anchor in text:
            text = text.replace(print_anchor, print_anchor + '        "historical_trade_case_mapping": next((x for x in report.get("checks", []) if x.get("name") == "formal_files:historical_trade_case_mapping"), {}),\n', 1)
    CHECK_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_experience()
    patch_consistency()
    print("historical trade CASE repair applied")
