import unittest
from pathlib import Path


class FormalDecisionCanonicalChainTests(unittest.TestCase):
    def test_protocol_uses_bds_then_formal_completion(self):
        protocol = Path("docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        index = Path("ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")
        for text in (protocol, index):
            self.assertIn("BUSINESS_DECISION_SOURCE", text)
            self.assertIn("SCHEDULED_REVIEW_COMPLETION", text)
            self.assertIn("formal_completion_path", text)
        self.assertIn("以下路径不得作为正式决策生产链", protocol)
        self.assertIn("SCHEDULED_REVIEW_COMPLETION", protocol)

    def test_three_entry_identity_contract_is_registered(self):
        protocol = Path("docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        for value in (
            "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            "CHATGPT_SCHEDULED_FORMAL_ANALYSIS",
            "MANUAL_CONVERSATION",
            "MANUAL_SCHEDULED_TASK",
            "AUTOMATIC_SCHEDULED_TASK",
        ):
            self.assertIn(value, protocol)

    def test_bds_top_level_formal_entry_identity_is_projected_into_source(self):
        source = Path("scripts/process_state_sync_request.py").read_text(encoding="utf-8")
        self.assertIn('"formal_entry_source"', source)
        self.assertIn("source_payload.setdefault(identity_field, request.get(identity_field))", source)

    def test_completion_builder_binds_registered_entry_identity(self):
        text = Path("scripts/manual_formal_decision_completion.py").read_text(encoding="utf-8")
        self.assertIn("Formal Decision completion requires one of the three registered entry identities", text)
        self.assertIn('"formal_entry_source": source_mode', text)
        self.assertIn('"entry_mode": entry_mode', text)
        self.assertIn('"bds_path": bds_path', text)
        self.assertIn('"content_hash": content_hash', text)

    def test_notification_workflow_requires_exact_formal_completion_path(self):
        workflow = Path(".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.formal_completion_path", workflow)
        self.assertIn("events/reviews/formal_*.json", workflow)
        self.assertIn("Project Formal Decision completion to canonical review event", workflow)
        self.assertIn("No current shared completion/report binding; fail closed without PushPlus.", workflow)


if __name__ == "__main__":
    unittest.main()

