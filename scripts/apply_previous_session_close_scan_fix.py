from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / "scripts/build_overseas_context.py"
    text = path.read_text(encoding="utf-8")
    old = '''        meta = result.get("meta", {})\n        previous_close = meta.get("chartPreviousClose")\n        if previous_close is None:\n            previous_close = meta.get("previousClose")\n        if previous_close is None:\n            previous_close = meta.get("regularMarketPreviousClose")\n'''
    new = '''        previous_close = None\n        close_values = quote.get("close") or []\n        latest_market_date = dt_local.date()\n        for j in range(idx - 1, -1, -1):\n            if j >= len(timestamps):\n                continue\n            bar_date = datetime.fromtimestamp(int(timestamps[j]), timezone.utc).astimezone(zone).date()\n            candidate = close_values[j] if j < len(close_values) else None\n            if bar_date < latest_market_date and candidate is not None:\n                previous_close = candidate\n                break\n'''
    if text.count(old) != 1:
        raise RuntimeError(f"anchor count={text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("previous close now scans prior local trading session in existing 5d payload")


if __name__ == "__main__":
    main()
