from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "ETF_SYSTEM_INDEX.md"
text = path.read_text(encoding="utf-8")
old = "- 账户/Dashboard/成交/正式复盘异步同步处理：`scripts/process_state_sync_request.py`\n"
new = (
    "- 账户/Dashboard/成交/正式复盘异步同步处理：`scripts/process_state_sync_request.py`；三份人类可读正式事实文件的低层写入统一经过 `scripts/formal_file_mutation_gateway.py`，账户同步由 `scripts/sync_formal_files.py` 负责，已确认成交费用纠错由 `scripts/apply_trade_fact_correction.py` 负责；gateway 不产生交易结论且禁止写 `ETF规则_MASTER.md`\n"
)
if old not in text:
    raise RuntimeError("system index formal-maintenance anchor missing")
if text.count(old) != 1:
    raise RuntimeError("system index formal-maintenance anchor is not unique")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("ETF_SYSTEM_INDEX formal mutation gateway entry updated")
