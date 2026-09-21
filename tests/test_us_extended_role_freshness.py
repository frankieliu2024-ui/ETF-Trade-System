import unittest
import unittest.mock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from scripts.check_system_consistency_core import validate_us_extended_live_freshness
from scripts import check_system_consistency as consistency

NY = ZoneInfo("America/New_York")


def ts(local_iso: str) -> int:
    return int(datetime.fromisoformat(local_iso).replace(tzinfo=NY).astimezone(timezone.utc).timestamp())


def record(role, local_iso, *, freshness="FRESH", quality="PASS", conditional=False):
    return {
        "reference_role": role,
        "conditional_industry_object": conditional,
        "quality_status": quality,
        "freshness_status": freshness,
        "latest": {"timestamp": ts(local_iso)},
    }


class UsExtendedRoleFreshnessTests(unittest.TestCase):
    def context(self, **objects):
        return {"objects": objects}

    def test_premarket_uses_proxies_and_accepts_prior_cash_index_reference(self):
        now = datetime.fromisoformat("2026-09-21T08:10:00-04:00")
        ctx = self.context(
            QQQ=record("NASDAQ100_EXTENDED_HOURS_PROXY", "2026-09-21T08:05:00"),
            SOXX=record("SEMICONDUCTOR_EXTENDED_HOURS_PROXY", "2026-09-21T08:05:00"),
            NDX=record("FORMAL_US_CASH_INDEX", "2026-09-18T16:00:00", freshness="PREVIOUS_SESSION_REFERENCE"),
            SOX=record("FORMAL_US_CASH_INDEX", "2026-09-18T16:00:00", freshness="PREVIOUS_SESSION_REFERENCE"),
        )
        self.assertTrue(validate_us_extended_live_freshness(ctx, now.astimezone(timezone.utc), 900)[0])

    def test_premarket_stale_required_proxy_fails(self):
        now = datetime.fromisoformat("2026-09-21T08:30:00-04:00")
        ctx = self.context(
            QQQ=record("NASDAQ100_EXTENDED_HOURS_PROXY", "2026-09-21T08:00:00", freshness="STALE"),
            SOXX=record("SEMICONDUCTOR_EXTENDED_HOURS_PROXY", "2026-09-21T08:25:00"),
        )
        self.assertFalse(validate_us_extended_live_freshness(ctx, now.astimezone(timezone.utc), 900)[0])

    def test_regular_requires_fresh_cash_indices(self):
        now = datetime.fromisoformat("2026-09-21T10:30:00-04:00")
        ctx = self.context(
            QQQ=record("NASDAQ100_EXTENDED_HOURS_PROXY", "2026-09-21T08:00:00", freshness="STALE"),
            SOXX=record("SEMICONDUCTOR_EXTENDED_HOURS_PROXY", "2026-09-21T08:00:00", freshness="STALE"),
            NDX=record("FORMAL_US_CASH_INDEX", "2026-09-21T10:25:00"),
            SOX=record("FORMAL_US_CASH_INDEX", "2026-09-21T09:00:00", freshness="STALE"),
        )
        self.assertFalse(validate_us_extended_live_freshness(ctx, now.astimezone(timezone.utc), 900)[0])

    def test_postmarket_uses_extended_proxies(self):
        now = datetime.fromisoformat("2026-09-21T17:10:00-04:00")
        ctx = self.context(
            QQQ=record("NASDAQ100_EXTENDED_HOURS_PROXY", "2026-09-21T17:05:00"),
            SOXX=record("SEMICONDUCTOR_EXTENDED_HOURS_PROXY", "2026-09-21T17:05:00"),
            NDX=record("FORMAL_US_CASH_INDEX", "2026-09-21T16:00:00", freshness="SESSION_REFERENCE"),
            SOX=record("FORMAL_US_CASH_INDEX", "2026-09-21T16:00:00", freshness="SESSION_REFERENCE"),
        )
        self.assertTrue(validate_us_extended_live_freshness(ctx, now.astimezone(timezone.utc), 900)[0])

    def test_conditional_industry_object_is_required_when_present(self):
        now = datetime.fromisoformat("2026-09-21T08:10:00-04:00")
        ctx = self.context(
            QQQ=record("NASDAQ100_EXTENDED_HOURS_PROXY", "2026-09-21T08:05:00"),
            SOXX=record("SEMICONDUCTOR_EXTENDED_HOURS_PROXY", "2026-09-21T08:05:00"),
            NVDA=record("CONDITIONAL_US_INDUSTRY_STOCK", "2026-09-21T07:00:00", freshness="STALE", conditional=True),
        )
        self.assertFalse(validate_us_extended_live_freshness(ctx, now.astimezone(timezone.utc), 900)[0])


    def test_pure_role_aware_pulse_age_is_observability_warning(self):
        detail = "phase=PRE_MARKET required=['QQQ', 'SOXX'] failures=['QQQ:NASDAQ100_EXTENDED_HOURS_PROXY:age=1198:freshness=FRESH', 'SOXX:SEMICONDUCTOR_EXTENDED_HOURS_PROXY:age=1198:freshness=FRESH'] limit=900"
        report = {"checks": [{"name": "us_extended:live_freshness", "status": "FAIL", "detail": detail}], "errors": ["us_extended:live_freshness: " + detail], "warnings": []}
        consistency._normalize_us_phase_freshness(report)
        self.assertEqual(report["checks"][0]["status"], "WARNING")
        self.assertEqual(report["hard_error_count"], 0)

    def test_nonfresh_role_aware_proxy_remains_hard_failure(self):
        detail = "phase=PRE_MARKET required=['QQQ', 'SOXX'] failures=['QQQ:NASDAQ100_EXTENDED_HOURS_PROXY:age=1198:freshness=STALE'] limit=900"
        report = {"checks": [{"name": "us_extended:live_freshness", "status": "FAIL", "detail": detail}], "errors": ["us_extended:live_freshness: " + detail], "warnings": []}
        with unittest.mock.patch.object(consistency, "_read_json", return_value={"objects": {}}):
            consistency._normalize_us_phase_freshness(report)
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertEqual(len(report["errors"]), 1)

    def test_missing_role_aware_proxy_remains_hard_failure(self):
        detail = "phase=PRE_MARKET required=[] failures=[] limit=900"
        report = {"checks": [{"name": "us_extended:live_freshness", "status": "FAIL", "detail": detail}], "errors": ["us_extended:live_freshness: " + detail], "warnings": []}
        with unittest.mock.patch.object(consistency, "_read_json", return_value={"objects": {}}):
            consistency._normalize_us_phase_freshness(report)
        self.assertEqual(report["checks"][0]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
