from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Close the last stale human-readable count left after adding the 2026-08-27 trade.
exp = ROOT / 'ETF交易复盘与经验库_2026.md'
text = exp.read_text(encoding='utf-8')
text = text.replace('不属于26笔证券交易', '不属于27笔证券交易')
exp.write_text(text, encoding='utf-8')

# Make the unique human-readable transaction index part of normal trade-event synchronization.
p = ROOT / 'scripts/process_state_sync_request.py'
s = p.read_text(encoding='utf-8')
if 'def sync_experience_transaction_index(event: dict) -> None:' not in s:
    marker = '\ndef write_trade_review_required(event: dict) -> None:\n'
    helper = r'''
def sync_experience_transaction_index(event: dict) -> None:
    """Upsert a confirmed trade into Experience §2.1 and keep its counts aligned.

    This is fact synchronization only. It never evaluates trade quality, changes
    lifecycle rules, or creates a trading action.
    """
    import re

    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    text = EXPERIENCE.read_text(encoding="utf-8")
    section_end = "\n### 2.2 银证转账与非交易现金流水"
    if section_end not in text:
        raise RuntimeError("experience transaction-index boundary missing")

    marker = f"TRADE_EVENT:{event_id}"
    if marker not in text:
        confirmed = str(event.get("confirmed_at_beijing") or "")
        dt = confirmed.replace("T", " ")[:19]
        name = str(event.get("name") or event.get("code") or "")
        code = str(event.get("code") or "")
        side = str(event.get("side") or "").upper()
        side_cn = "买入" if side in {"BUY", "B", "买入", "买"} else "卖出" if side in {"SELL", "S", "卖出", "卖"} else side
        qty = int(float(event.get("quantity") or 0))
        price = float(event.get("price") or 0)
        gross = float(event.get("amount") or price * qty)
        fee = safe_float(event.get("fee_amount"))
        fee_status = str(event.get("fee_status") or "").upper()
        fee_confirmed = fee is not None and fee_status in {"CONFIRMED", "KNOWN", "FINAL"}
        fee_text = f"{fee:.2f}" if fee_confirmed else "待确认"
        if side_cn == "买入":
            cash = -(gross + (fee or 0 if fee_confirmed else 0))
        elif side_cn == "卖出":
            cash = gross - (fee or 0 if fee_confirmed else 0)
        else:
            cash = 0.0
        cash_text = f"{cash:,.2f}" if fee_confirmed else f"{cash:,.2f}（未含待确认费用）"
        lifecycle = str(event.get("lifecycle") or "待确认")
        linked = str(event.get("linked_decision_id") or "")
        note = lifecycle + (f"；关联决策{linked}" if linked else "") + "；真实成交已执行"
        row = f"|{dt}|{name}（{code}）|{code}|{side_cn}|{qty:,}|{price:.3f}|{gross:,.2f}|{fee_text}|{cash_text}|{note}| <!-- {marker} -->"
        idx = text.index(section_end)
        text = text[:idx].rstrip() + "\n" + row + "\n" + text[idx:]

    # Recompute the unique index counts from the actual table instead of carrying
    # a hand-maintained number that can lag a newly confirmed fill.
    table_start = text.index("|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|")
    table_end = text.index(section_end, table_start)
    rows = []
    for line in text[table_start:table_end].splitlines():
        if re.match(r"^\|20\d{2}-\d{2}-\d{2} ", line):
            rows.append(line)
    total = len(rows)
    etf_count = sum("ETF（" in line for line in rows)
    stock_count = total - etf_count
    dates = [line.split("|")[1][:10] for line in rows]
    last_date = max(dates) if dates else ""
    text = re.sub(
        r"统计区间为2026-07-13至\d{4}-\d{2}-\d{2}，共\d+笔证券交易：ETF \d+笔、个股\d+笔。",
        f"统计区间为2026-07-13至{last_date}，共{total}笔证券交易：ETF {etf_count}笔、个股{stock_count}笔。",
        text,
        count=1,
    )
    text = re.sub(r"不属于\d+笔证券交易", f"不属于{total}笔证券交易", text, count=1)
    EXPERIENCE.write_text(text, encoding="utf-8")
'''
    if marker not in s:
        raise SystemExit('write_trade_review_required insertion marker not found')
    s = s.replace(marker, '\n' + helper + marker, 1)

call_anchor = '        EXPERIENCE.write_text(upsert_managed_line(EXPERIENCE.read_text(encoding="utf-8"), CASE_START, CASE_END, event_id, case_line), encoding="utf-8")\n'
call_line = '        sync_experience_transaction_index(event)\n'
if call_line not in s:
    if call_anchor not in s:
        raise SystemExit('experience managed-line call anchor not found')
    s = s.replace(call_anchor, call_anchor + call_line, 1)
p.write_text(s, encoding='utf-8')

print('finalized human-readable transaction-index synchronization')
