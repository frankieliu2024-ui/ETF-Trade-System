from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_experience() -> None:
    path = ROOT / "ETF交易复盘与经验库_2026.md"
    text = path.read_text(encoding="utf-8")

    case_heading = "### 2.12 CASE-20260827-01：通信ETF风险观察区主动Trial"
    if case_heading not in text:
        anchor = "### 2.12 CASE系统贡献索引"
        if anchor not in text:
            raise RuntimeError("experience CASE contribution anchor missing")
        case = '''### 2.12 CASE-20260827-01：通信ETF风险观察区主动Trial

- 背景／生命周期：2026-08-27上午ETF策略仍处风险观察区，正式风险许可为“允许Trial”、Confirm关闭。09:56:49形成通信ETF（515880）5,000元Trial正式决策，目标是用有限风险验证通信方向在A股成长修复中的独立强势是否能够持续，而不是追求一次性方向预测。
- 关键证据：决策时通信ETF（515880）价格0.673元、当日涨幅约3.86%，在当时11只ETF横截面中涨幅居首；相对ETF横截面中位数约+3.59个百分点、相对上证指数约+3.75个百分点、相对创业板指约+3.12个百分点。上述排名只作为描述证据；正式决策同时依赖A股科创成长正向本地反馈、目标ETF自身强度延续及风险观察区单次5,000元Trial的信息购买边界。
- 执行：10:08:43买入通信ETF（515880）7,400份，成交价0.671元，成交本金4,965.40元；关联正式决策`20260827_095649_515880_trial_5000`。从决策到成交约714秒，成交价较决策时0.673元低约0.30%，未出现不利追价；本次交易费用仍待券商事实确认。
- 结果：真实成交已经执行，生命周期正式进入Trial；截至本CASE建立时尚未到达后续T+验证节点，不提前写Trial成功、失败、Confirm或退出结论。
- 判断质量：进行中。事前决策符合风险观察区“允许Trial、Confirm关闭”和固定5,000元Trial边界，并有目标ETF自身反馈；是否属于高质量机会仍须由后续Trial验证结果检验，不能用决策后行情倒推。
- 执行质量：初步合格。成交本金落在5,000元Trial档内，成交价格优于正式决策时价格，且真实成交已及时回写持仓；费用未确认是尚未闭环的执行事实，不影响成交方向和数量确认。
- 风险收益质量：待验证。该笔新增资本的设计目的仅为购买信息，没有扩大为Confirm；后续若结构、承接、相对强弱或独立假设弱化，应按MASTER重新评估继续持有、降低风险或退出。
- 资本使用效率：待验证。资金来自既有现金，没有为本次Trial机械卖出旧仓；后续须继续与现金、全部持仓ETF、全部观察ETF及可释放资本统一比较，判断这4,965.40元继续占用是否仍优于替代用途。
- 最终结果／反事实：本CASE尚未关闭。当前只允许记录“执行了一个合规Trial并取得真实持仓反馈入口”；不使用10:08:43之后的未来行情构造“如果不买/更早买/更晚买”的事后最优结论。
- 评价：进行中。当前可确认的是风险许可、机会判断、金额和人工执行链已经闭环；收益结果、Trial信息价值和后续生命周期质量留待T+节点、实际增减仓或假设变化时更新。
- 经验：真实Trial成交本身就是需要连续跟踪的CASE起点，不能只停留在“待复盘CASE”自动入口；但CASE建立也不等于规则升级，单笔结果不得修改MASTER。
- 系统贡献：验证已有规则（进行中）；同时用于检查真实成交→正式CASE→后续生命周期复盘是否完整闭环。

'''
        text = text.replace(anchor, case + "### 2.13 CASE系统贡献索引", 1)

    row = "|CASE-20260827-01|验证已有规则|风险观察区5,000元Trial真实执行已进入正式CASE；后续验证信息购买价值、执行质量和生命周期闭环|"
    if row not in text:
        anchor = "|CASE-20260819-01|验证已有规则|验证风险观察下0元决议和生命周期独立管理|"
        if anchor not in text:
            raise RuntimeError("CASE contribution row anchor missing")
        text = text.replace(anchor, anchor + "\n" + row, 1)

    old_intake = "20260827_100843_515880_buy｜- 待复盘CASE｜2026-08-27T10:08:43+08:00｜通信ETF（515880）｜BUY 7,400份｜生命周期：Trial｜成交价0.671元、成交本金4,965.40元；关联09:56:49正式Trial决策；费用待确认；仅登记真实成交与执行事实，判断质量、Trial验证结果及后续生命周期结论留待盘后/后续节点形成。"
    new_intake = "20260827_100843_515880_buy｜- 已归入CASE-20260827-01｜2026-08-27T10:08:43+08:00｜通信ETF（515880）｜BUY 7,400份｜生命周期：Trial｜成交价0.671元、成交本金4,965.40元；关联09:56:49正式Trial决策；费用待确认；CASE已建立并按后续T+节点、实际增减仓或假设变化继续更新。"
    if old_intake in text:
        text = text.replace(old_intake, new_intake, 1)
    elif new_intake not in text:
        raise RuntimeError("515880 AUTO_CASE intake line missing")

    maintenance = "|2026-08-27|通信ETF（515880）Trial CASE与正式成交闭环维护|新增CASE-20260827-01并将自动待复盘入口升级为正式CASE关联；同步建立README防版本漂移和CASE完整性一致性门禁，不修改交易规则、金额档或自动交易权限|"
    if maintenance not in text:
        anchor = "|2026-08-26|正式文件一致性与数据口径审计|同步当前行情主备源、跨市场固定时点、直接Confirm合法入口及8月25日已确认费用；新增一致性防回归检查，不新增金额档、风险级别或自动交易权限|"
        if anchor not in text:
            raise RuntimeError("experience maintenance anchor missing")
        text = text.replace(anchor, anchor + "\n" + maintenance, 1)

    path.write_text(text, encoding="utf-8")


def patch_index() -> None:
    path = ROOT / "ETF_SYSTEM_INDEX.md"
    text = path.read_text(encoding="utf-8")
    old = "若存在用户确认或券商事实确认的真实成交，除更新账户事实和Dashboard外，同时生成 `events/trades/` 成交事件，在行情档案登记客观成交，并在经验库生成待复盘CASE入口；"
    new = "若存在用户确认或券商事实确认的真实成交，除更新账户事实和Dashboard外，同时生成 `events/trades/` 成交事件，在行情档案登记客观成交，并在经验库建立正式CASE入口；CASE可以先以进行中状态记录已知事前证据和真实执行，但不得只停留在“待复盘CASE”占位；"
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("index trade CASE rule marker missing")

    marker = "历史聊天不属于正式来源。发生冲突时遵循 MASTER > Dashboard > 当前行情与成交 > 经验库 > 行情档案 > 历史聊天。"
    addition = marker + "\n\n仓库首页 `README.md` 只承担导航和运行说明，不复制正式系统版本号、provider优先级或其他容易随生产状态变化的事实；当前正式版本只从 `ETF规则_MASTER.md` 读取。README导航完整性和禁止硬编码版本由系统一致性检查维护。"
    if "README.md` 只承担导航和运行说明" not in text:
        if marker not in text:
            raise RuntimeError("index README insertion marker missing")
        text = text.replace(marker, addition, 1)

    old_check = "- 四个正式文件与数据规范；"
    new_check = "- 四个正式文件、README导航与数据规范；"
    if old_check in text:
        text = text.replace(old_check, new_check, 1)
    path.write_text(text, encoding="utf-8")


def patch_consistency() -> None:
    path = ROOT / "scripts/check_system_consistency.py"
    text = path.read_text(encoding="utf-8")

    old_case_logic = '''        if f"{event_id}｜" not in experience:\n            errors.append(f"{event_id}:experience_case")\n        confirmed = str(event.get("confirmed_at_beijing") or "")[:10]\n        if confirmed >= "2026-08-27" and f"TRADE_EVENT:{event_id}" not in experience:\n            errors.append(f"{event_id}:experience_transaction_index")\n'''
    new_case_logic = '''        if f"{event_id}｜" not in experience:\n            errors.append(f"{event_id}:experience_case_intake")\n        confirmed = str(event.get("confirmed_at_beijing") or "")[:10]\n        if confirmed >= "2026-08-27":\n            if f"TRADE_EVENT:{event_id}" not in experience:\n                errors.append(f"{event_id}:experience_transaction_index")\n            import re\n            mapping = re.search(rf"^{re.escape(event_id)}｜- 已归入(CASE-\\d{{8}}-\\d{{2}})｜", experience, re.MULTILINE)\n            if not mapping:\n                errors.append(f"{event_id}:formal_case_mapping")\n            elif mapping.group(1) not in experience or f"### " not in experience[:experience.find(mapping.group(1)) + 4]:\n                errors.append(f"{event_id}:formal_case_section")\n'''
    if old_case_logic in text:
        text = text.replace(old_case_logic, new_case_logic, 1)
    elif "formal_case_mapping" not in text:
        raise RuntimeError("trade formal sync logic marker missing")

    readme_func = '''\n\ndef _validate_readme_front_door(report: dict) -> None:\n    import re\n\n    path = ROOT / "README.md"\n    errors = []\n    if not path.exists():\n        errors.append("missing")\n        readme = ""\n    else:\n        readme = path.read_text(encoding="utf-8")\n    first_line = readme.splitlines()[0].strip() if readme.splitlines() else ""\n    if first_line != "# ETF Trade System":\n        errors.append(f"unexpected_h1={first_line}")\n    if re.search(r"ETF Trade System\\s+V\\d+\\.\\d+\\.\\d+", readme, re.IGNORECASE):\n        errors.append("hardcoded_system_version")\n    required = [\n        "ETF规则_MASTER.md",\n        "ETF_SYSTEM_INDEX.md",\n        "ETF当前状态_DASHBOARD.md",\n        "ETF交易复盘与经验库_2026.md",\n        "ETF市场行情档案_2026.md",\n        "ETF与市场监测数据接口使用规范.md",\n    ]\n    missing_links = [name for name in required if name not in readme]\n    if missing_links:\n        errors.append("missing_links=" + ",".join(missing_links))\n    status = "FAIL" if errors else "PASS"\n    report.setdefault("checks", []).append({\n        "name": "readme:canonical_front_door",\n        "status": status,\n        "detail": "README uses MASTER as sole formal version source" if not errors else ";".join(errors),\n    })\n    for item in errors:\n        message = "readme_front_door:" + item\n        if message not in report.setdefault("errors", []):\n            report["errors"].append(message)\n    report["hard_error_count"] = len(report.get("errors") or [])\n    report["warning_count"] = len(report.get("warnings") or [])\n    report["status"] = "FAIL" if report["hard_error_count"] else ("WARNING" if report["warning_count"] else "PASS")\n'''
    if "def _validate_readme_front_door" not in text:
        anchor = "\ndef _validate_production_mutation_protocol(report: dict) -> None:\n"
        if anchor not in text:
            raise RuntimeError("consistency function insertion anchor missing")
        text = text.replace(anchor, readme_func + anchor, 1)

    if "    _validate_readme_front_door(report)\n" not in text:
        old = "    _validate_trade_event_formal_sync(report)\n    _validate_production_mutation_protocol(report)\n"
        new = "    _validate_trade_event_formal_sync(report)\n    _validate_readme_front_door(report)\n    _validate_production_mutation_protocol(report)\n"
        if old not in text:
            raise RuntimeError("consistency main call anchor missing")
        text = text.replace(old, new, 1)

    old_print = '        "formal_trade_event_sync": next((x for x in report.get("checks", []) if x.get("name") == "formal_files:executed_trade_event_sync"), {}),\n'
    new_print = old_print + '        "readme_front_door": next((x for x in report.get("checks", []) if x.get("name") == "readme:canonical_front_door"), {}),\n'
    if "\"readme_front_door\":" not in text:
        if old_print not in text:
            raise RuntimeError("consistency print anchor missing")
        text = text.replace(old_print, new_print, 1)

    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_experience()
    patch_index()
    patch_consistency()
    print("patched formal CASE, README governance and consistency gates")


if __name__ == "__main__":
    main()
