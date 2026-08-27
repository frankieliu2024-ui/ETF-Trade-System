from pathlib import Path

p = Path('scripts/sync_formal_files.py')
s = p.read_text(encoding='utf-8')
old = '        f"|本次账户事实已确认费用|{money(confirmed_account_fees)}（仅展示本次确认，权益状态按既有账本维护）|",\n'
new = '        f"|账户事实内已确认费用记录合计|{money(confirmed_account_fees)}（仅统计account_fact中明确标记CONFIRMED的记录；不代表当前最新一笔成交费用）|",\n'
if old not in s:
    raise SystemExit('fee label generator marker not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

d = Path('ETF当前状态_DASHBOARD.md')
t = d.read_text(encoding='utf-8')
t = t.replace('|本次账户事实已确认费用|5.00元（仅展示本次确认，权益状态按既有账本维护）|', '|账户事实内已确认费用记录合计|5.00元（仅统计account_fact中明确标记CONFIRMED的记录；不代表通信ETF（515880）本次买入费用，本次买入费用仍待确认）|')
d.write_text(t, encoding='utf-8')
print('clarified account fee label')
