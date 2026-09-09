import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.emergency_market_evidence import validate_external_market_evidence
from scripts.runtime_session_gate import classify_live_snapshot_request


class EmergencyMarketEvidenceTests(unittest.TestCase):
    def _root(self, directory):
        root = Path(directory)
        (root / "requests" / "live_snapshot").mkdir(parents=True)
        (root / "config" / "market").mkdir(parents=True)
        (root / "data" / "state").mkdir(parents=True)
        (root / "config" / "runtime_policy.json").write_text(json.dumps({
            "fresh_max_age_seconds": 900, "degraded_max_age_seconds": 1500
        }), encoding="utf-8")
        (root / "config" / "market" / "provider_priority.json").write_text(json.dumps({
            "providers": {"tencent_qq": {"approved_direct_hosts": ["qt.gtimg.cn"]}},
            "objects": {"515220.SH": ["tencent_qq"]}
        }), encoding="utf-8")
        return root

    def _request(self, root, **changes):
        provider_time = "2026-09-09T10:24:30+08:00"
        req = {
            "request_id": "emergency-1", "evidence_id": "evidence-1",
            "request_type": "EMERGENCY_EXTERNAL_MARKET_EVIDENCE",
            "source": "CHATGPT_WEB_DIRECT_PROVIDER", "market_date": "2026-09-09",
            "market_phase": "REGULAR", "requested_at_beijing": "2026-09-09T10:25:00+08:00",
            "retrieved_at_beijing": "2026-09-09T10:25:00+08:00",
            "evidence_available_at_beijing": "2026-09-09T10:25:00+08:00",
            "consumed_external_market_evidence": "requests/live_snapshot/emergency-1.json",
            "decision_critical_symbols": ["515220.SH"],
            "rows": [{
                "symbol": "515220.SH", "name": "煤炭ETF", "provider": "tencent_qq",
                "provider_source_url": "https://qt.gtimg.cn/q=sh515220",
                "provider_as_of_beijing": provider_time,
                "provider_timestamp": int(datetime.fromisoformat(provider_time).timestamp() * 1000),
                "market_phase": "REGULAR", "close": 1.23, "open": 1.20, "high": 1.24,
                "low": 1.19, "prev_close": 1.18, "volume": 1000, "amount": 1230,
                "source_type": "DIRECT_PROVIDER"
            }]
        }
        req.update(changes)
        path = root / "requests" / "live_snapshot" / "emergency-1.json"
        path.write_text(json.dumps(req), encoding="utf-8")
        return req

    def test_valid_evidence_is_state_sync_only_and_passes(self):
        with TemporaryDirectory() as d:
            root = self._root(d); req = self._request(root)
            self.assertEqual(classify_live_snapshot_request(req), "STATE_SYNC_ONLY")
            result = validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")
            self.assertEqual(result["validation_status"], "PASS")

    def test_rejects_wrong_host_and_future_fact(self):
        with TemporaryDirectory() as d:
            root = self._root(d); req = self._request(root)
            req["rows"][0]["provider_source_url"] = "https://example.com/quote"
            with self.assertRaisesRegex(ValueError, "HOST_INVALID"):
                validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")
            req = self._request(root)
            req["rows"][0]["provider_as_of_beijing"] = "2026-09-09T10:26:00+08:00"
            with self.assertRaisesRegex(ValueError, "TIME_AFTER_CUTOFF"):
                validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")

    def test_rejects_missing_critical_object_and_non_direct_source(self):
        with TemporaryDirectory() as d:
            root = self._root(d)
            req = self._request(root, decision_critical_symbols=["515220.SH", "999999.SH"])
            with self.assertRaisesRegex(ValueError, "OBJECT_MISSING"):
                validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")
            req = self._request(root)
            req["rows"][0]["source_type"] = "SEARCH_SNIPPET"
            with self.assertRaisesRegex(ValueError, "SOURCE_NOT_DIRECT"):
                validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")

    def test_rejects_path_or_clock_mismatch(self):
        with TemporaryDirectory() as d:
            root = self._root(d); req = self._request(root)
            req["consumed_external_market_evidence"] = "requests/live_snapshot/other.json"
            with self.assertRaisesRegex(ValueError, "PATH_NOT_CURRENT"):
                validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")
            req = self._request(root)
            req["evidence_available_at_beijing"] = "2026-09-09T10:25:20+08:00"
            with self.assertRaisesRegex(ValueError, "NOT_AVAILABLE"):
                validate_external_market_evidence(req, root, decision_time="2026-09-09T10:25:00+08:00", availability_time="2026-09-09T10:25:10+08:00", ingress_path="requests/live_snapshot/emergency-1.json")

    def test_existing_request_without_evidence_remains_refresh_bearing(self):
        self.assertEqual(classify_live_snapshot_request({"request_type": "QUERY_TIME_REFRESH"}), "REFRESH_BEARING")


if __name__ == "__main__":
    unittest.main()
