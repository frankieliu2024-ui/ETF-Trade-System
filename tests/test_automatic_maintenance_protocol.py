import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AutomaticMaintenanceProtocolTests(unittest.TestCase):
    def test_protocol_and_machine_mirror_exist(self):
        protocol = ROOT / "docs/ETF自动维护与运行保障协议_V1.0.md"
        mirror = ROOT / "config/maintenance/automatic_maintenance_protocol.json"
        self.assertTrue(protocol.exists())
        data = json.loads(mirror.read_text(encoding="utf-8"))
        self.assertEqual(data["role"], "RUNTIME_ROUTING_AND_CLOSURE_CONTRACT_NOT_BUSINESS_RULE_SOURCE")
        self.assertEqual(len(data["scenarios"]), 9)

    def test_protocol_preserves_single_owner_and_no_parallel_chain(self):
        text = (ROOT / "docs/ETF自动维护与运行保障协议_V1.0.md").read_text(encoding="utf-8")
        for phrase in ("唯一canonical owner", "不得为以上场景新增第二个producer", "MASTER", "回读"):
            self.assertIn(phrase, text)
        self.assertIn("MAINTENANCE_INCOMPLETE", text)


if __name__ == "__main__":
    unittest.main()
