from pathlib import Path
import json, sys
ROOT=Path(__file__).resolve().parents[1]
arc=(ROOT/'ETF市场行情档案_2026.md').read_text(encoding='utf-8')
exp=(ROOT/'ETF交易复盘与经验库_2026.md').read_text(encoding='utf-8')
errors=[]
count=0
for path in sorted((ROOT/'events/trades').glob('*.json')):
    e=json.loads(path.read_text(encoding='utf-8'))
    if str(e.get('execution_status','')).upper()!='EXECUTED': continue
    eid=str(e.get('event_id') or '')
    if not eid: continue
    count += 1
    if f'{eid}｜' not in arc: errors.append(f'{eid}: missing keyed archive trade fact')
    if f'{eid}｜' not in exp: errors.append(f'{eid}: missing keyed experience CASE intake')
if (ROOT/'events/trades/20260827_100843_515880_buy.json').exists() and 'TRADE_EVENT:20260827_100843_515880_buy' not in exp:
    errors.append('20260827_100843_515880_buy: missing human-readable transaction-index row')
if errors:
    print(json.dumps({'status':'FAIL','errors':errors},ensure_ascii=False)); sys.exit(1)
print(json.dumps({'status':'PASS','executed_trade_events_checked':count},ensure_ascii=False))
