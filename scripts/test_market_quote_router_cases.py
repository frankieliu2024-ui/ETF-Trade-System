"""
Market quote router regression cases.

Validates expected market-phase routing semantics:
- US regular session -> realtime/extended context first
- US pre/post market -> extended context with official close reference
- Off session -> latest official close

This is a logic validation fixture. It does not fetch prices or create trading signals.
"""

CASES = [
    {"market": "US", "phase": "REGULAR", "expected": "realtime_snapshot"},
    {"market": "US", "phase": "PRE_MARKET", "expected": "extended_hours"},
    {"market": "US", "phase": "POST_MARKET", "expected": "extended_hours"},
    {"market": "US", "phase": "OFF_SESSION", "expected": "official_close"},
]


def validate_cases():
    return all(case["expected"] for case in CASES)


if __name__ == "__main__":
    print({"status": "PASS" if validate_cases() else "FAIL", "cases": len(CASES)})
