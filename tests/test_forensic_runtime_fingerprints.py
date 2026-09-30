import contextlib
import io
import os
import unittest

from scripts.business_decision_source import project_decision_response


class ForensicRuntimeFingerprintTests(unittest.TestCase):
    def test_instrumentation_does_not_change_projection(self):
        source = {"request_type": "BUSINESS_DECISION_SOURCE", "request_id": "x", "parent_request_id": "p", "decision_id": "d", "consumed_snapshot": "s"}
        work_package = {"problem_graph": [{"problem_id": "RISK_PERMISSION"}]}
        response = {"answers": {"RISK_PERMISSION": {
            "final_action": "允许Trial",
            "capital_comparison": "已完成",
            "next_change_condition": "条件",
            "evidence_decision_impact": "无",
        }}}
        with contextlib.redirect_stderr(io.StringIO()):
            os.environ.pop("ETF_FORENSIC_FINGERPRINT", None)
            baseline = project_decision_response(source, response, work_package)
            os.environ["ETF_FORENSIC_FINGERPRINT"] = "1"
            observed = project_decision_response(source, response, work_package)
            os.environ.pop("ETF_FORENSIC_FINGERPRINT", None)
        self.assertEqual(observed, baseline)
        self.assertEqual(observed["fingerprint"], baseline["fingerprint"])


if __name__ == "__main__":
    unittest.main()
