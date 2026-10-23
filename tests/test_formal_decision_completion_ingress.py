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

    def test_formal_adapter_delegates_to_shared_completion_owner(self):
        adapter = Path("scripts/manual_formal_decision_completion.py").read_text(encoding="utf-8")
        shared = Path("scripts/scheduled_review_completion.py").read_text(encoding="utf-8")
        self.assertIn("Compatibility facade delegating Formal completion to the shared owner", adapter)
        self.assertIn("from scheduled_review_completion import build_formal_decision_completion_request as shared_builder", adapter)
        self.assertIn("def build_formal_decision_completion_request(", shared)
        self.assertIn('"formal_fact_type": "FORMAL_POST_CLOSE_REVIEW"', shared)
        self.assertIn('"source": "CHATGPT_SCHEDULED_REVIEW_ACTOR"', shared)
        self.assertIn('"bds_path": bds_path', shared)
        self.assertIn('"content_hash": content_hash', shared)

    def test_notification_workflow_requires_exact_formal_completion_path(self):
        workflow = Path(".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.formal_completion_path", workflow)
        self.assertIn("events/reviews/formal_*.json", workflow)
        self.assertIn("Project Formal Decision completion to canonical review event", workflow)
        self.assertIn("No current shared completion/report binding; fail closed without PushPlus.", workflow)

    def test_protocol_defines_semantic_formal_numbering(self):
        protocol = Path("docs/Formal Decision执行与用户回复协议.md").read_text(encoding="utf-8")
        self.assertIn("单独只有一段的说明、置信度或“最可能出错的地方”不得机械添加“1.”", protocol)
        self.assertIn("全市场发现和观察ETF的概述段不属于候选对象", protocol)


if __name__ == "__main__":
    unittest.main()



def test_completion_gateway_derives_only_same_parent_bds_path():
    source = Path("scripts/process_state_sync_request.py").read_text(encoding="utf-8")
    assert "derived_bds_path = f\"requests/live_snapshot/{parent_id}__business_decision_source.json\"" in source
    assert "never scan history or choose a lexicographic match" in source
