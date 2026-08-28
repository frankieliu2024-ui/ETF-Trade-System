from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    if s.count(old) != 1:
        raise RuntimeError(f"{path}: anchor count={s.count(old)}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


def main():
    replace_once(
        "scripts/build_overseas_context.py",
        '''        previous_close = None\n        close_values = quote.get("close") or []\n        for j in range(idx - 1, -1, -1):\n            candidate = close_values[j] if j < len(close_values) else None\n            if candidate is not None:\n                previous_close = candidate\n                break\n''',
        '''        meta = result.get("meta", {})\n        previous_close = meta.get("chartPreviousClose")\n        if previous_close is None:\n            previous_close = meta.get("previousClose")\n        if previous_close is None:\n            previous_close = meta.get("regularMarketPreviousClose")\n'''
    )
    replace_once(
        "tests/test_low_cost_alpha_evidence.py",
        '''            "meta": {"timezone": "America/New_York"},\n''',
        '''            "meta": {"timezone": "America/New_York", "chartPreviousClose": 101.0},\n'''
    )
    print("fixed Yahoo previous-close semantics")


if __name__ == "__main__":
    main()
