import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import scripts.build_query_context as query_context


class DecisionFirstQueryRefreshTests(unittest.TestCase):
    def test_refresh_assurance_precedes_full_decision_context(self):
        events = []
        current = {
            "market_date": "2026-09-05",
            "latest_snapshot": "data/market/snapshots/2026-09-05_093000.json",
            "captured_at": "2026-09-05T09:30:05+08:00",
            "data_freshness": {"captured_at_beijing": "2026-09-05T09:30:05+08:00", "provider_as_of": "2026-09-05T09:30:05+08:00"},
            "rules_version": "V2.2.31",
        }
        account = {"status": "VALID", "updated_at": "2026-09-04T15:03:00+08:00", "source": "test"}
        decision = {"rules_version": "V2.2.31", "generated_at": "2026-09-05T09:30:06+08:00"}
        request = {"request_id": "r1", "requested_at_beijing": "2026-09-05T09:30:00+08:00", "requested_by": "interactive"}

        def fake_read_json(path, default=None):
            if str(path).endswith("request.json"):
                return request
            if str(path).endswith("etf_monitor_universe.json"):
                return {"version": "u1", "objects": [{"code": "159326"}]}
            return {} if default is None else default

        def fake_current(root):
            events.append("current")
            return dict(current)

        def fake_account(root):
            events.append("account")
            return dict(account)

        def fake_refresh(*args, **kwargs):
            events.append("refresh")
            return {
                "refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH",
                "decision_freshness": {"status": "DIRECT", "post_request": True, "resolved_post_request": True},
                "quotes": [],
            }

        def fake_decision(root):
            events.append("decision")
            return dict(decision)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "request.json").write_text("{}", encoding="utf-8")
            with patch.object(query_context, "read_json", side_effect=fake_read_json),                  patch.object(query_context, "read_current", side_effect=fake_current),                  patch.object(query_context, "read_account_fact", side_effect=fake_account),                  patch.object(query_context, "build_market_quote_context", side_effect=fake_refresh),                  patch.object(query_context, "build_decision_context", side_effect=fake_decision):
                result = query_context.build(root, request_file="request.json")

        self.assertEqual(events[0], "refresh")
        self.assertLess(events.index("refresh"), events.index("decision"))
        self.assertLess(events.index("refresh"), events.index("account"))
        self.assertEqual(result["freshness_assurance"]["refresh_mode"], "QUERY_TIME_IMMEDIATE_REFRESH")
        self.assertEqual(result["decision_fact_pack"]["current"]["latest_snapshot"], current["latest_snapshot"])

    def test_fact_pack_keeps_request_and_pit_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            pack = query_context.build_decision_fact_pack(
                Path(directory),
                {"request_id": "r2", "requested_at_beijing": "2026-09-05T10:00:00+08:00", "requested_by": "scheduled"},
                {"market_date": "2026-09-05", "latest_snapshot": "snap.json", "captured_at": "2026-09-05T10:00:02+08:00"},
                {"status": "VALID", "updated_at": "2026-09-04T15:03:00+08:00", "source": "account"},
                {"rules_version": "V2.2.31", "generated_at": "2026-09-05T10:00:03+08:00"},
                {"refresh_mode": "CACHED_STATE", "decision_freshness": {"status": "DIRECT"}, "quotes": []},
            )
        self.assertEqual(pack["trigger"]["source"], "scheduled")
        self.assertEqual(pack["master"]["version"], "V2.2.31")
        self.assertEqual(pack["current"]["latest_snapshot"], "snap.json")
        self.assertEqual(pack["account_fact"]["as_of_beijing"], "2026-09-04T15:03:00+08:00")
        self.assertEqual(pack["etf_universe"]["count"], 0)

    def test_missing_latency_fields_remain_explicit(self):
        result = query_context.build_fast_path_latency(
            {"requested_at_beijing": "2026-09-05T10:00:00+08:00"},
            {"captured_at": "2026-09-05T10:00:02+08:00"},
            {"generated_at": "2026-09-05T10:00:03+08:00"},
            {"refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH", "decision_freshness": {"post_request": True}},
            "2026-09-05T10:00:04+08:00",
        )
        self.assertIsNone(result["refresh_start_latency"])
        self.assertEqual(result["total_fast_path_latency"], 4.0)
        self.assertEqual(result["t_new_current"], "2026-09-05T10:00:02+08:00")

    def test_latency_durations_are_non_negative_and_observed(self):
        result = query_context.build_fast_path_latency(
            {"requested_at_beijing": "2026-09-05T10:00:00+08:00", "refresh_started_at_beijing": "2026-09-05T10:00:01+08:00"},
            {"captured_at": "2026-09-05T10:00:03+08:00"},
            {"generated_at": "2026-09-05T10:00:04+08:00"},
            {"refresh_mode": "QUERY_TIME_IMMEDIATE_REFRESH", "decision_freshness": {"post_request": True}},
            "2026-09-05T10:00:05+08:00",
        )
        self.assertEqual(result["refresh_start_latency"], 1.0)
        self.assertEqual(result["refresh_duration"], 2.0)
        self.assertEqual(result["post_current_decision_latency"], 1.0)
        self.assertEqual(result["total_fast_path_latency"], 5.0)


if __name__ == "__main__":
    unittest.main()
