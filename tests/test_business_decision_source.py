import unittest

from scripts.business_decision_source import (
    BUSINESS_DECISION_SOURCE,
    STATE_SYNC_ONLY,
    build_formal_completion_from_source,
    classify_request,
    validate_source,
    _normalize_formal_presentation_layout,
    _validate_formal_presentation_contract,
)

class BusinessDecisionSourceTests(unittest.TestCase):
    def base(self):
        return {
            "request_type": BUSINESS_DECISION_SOURCE,
            "request_id": "r1",
            "parent_request_id": "r1",
            "decision_id": "d1",
            "formal_entry_source": "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "entry_mode": "MANUAL_CONVERSATION",
            "occurrence_id": "manual-occurrence",
            "market_date": "2026-10-16",
            "consumed_snapshot": "snap-1",
            "risk_permission": "PERMITTED",
            "main_candidate": "NONE",
            "opportunity_status": "无机会",
            "capital_use": "现金",
            "continued_holding_opportunity_cost": "低",
            "action_changes_now": "NO",
            "next_change_condition": "risk permission changes",
            "managed_position_reviews": [],
            "etf_opportunity_reviews": [],
            "capital_competition": [],
            "next_unit_capital_use": "现金",
            "decision_evidence_consumption": {
                "request_id": "r1",
                "layer_1_external_cross_market": "external/cross-market evidence reviewed",
                "layer_2_a_share_internal": "A-share index/structure/breadth feedback reviewed",
                "layer_3_etf_opportunity_capital": "Discovery/holdings/Observation/cash/releasable capital reviewed",
                "discovery_to_capital_competition_consumed": True,
                "all_managed_positions_sell_chain_consumed": True,
                "held_etf_additional_capital_consumed": True,
                "next_unit_capital_use_consumed": True,
            },
        }

    def test_explicit_classification_does_not_guess(self):
        self.assertEqual(classify_request(self.base()), BUSINESS_DECISION_SOURCE)
        legacy = dict(self.base())
        legacy["request_type"] = STATE_SYNC_ONLY
        self.assertEqual(classify_request(legacy), STATE_SYNC_ONLY)
        guessed = dict(self.base())
        guessed.pop("request_type")
        self.assertEqual(classify_request(guessed), "REFRESH_BEARING")

    def test_phase_one_reply_ready_without_formal_machine_fields(self):
        source = validate_source(self.base(), expected_snapshot="snap-1")
        self.assertTrue(source["source_durable"])
        self.assertTrue(source["reply_ready"])
        self.assertNotIn("formal_completion_validator", source)

    def test_entry_identity_mismatch_fails_closed(self):
        source = self.base()
        source["formal_entry_source"] = "CHATGPT_SCHEDULED_FORMAL_ANALYSIS"
        source["entry_mode"] = "MANUAL_CONVERSATION"
        with self.assertRaisesRegex(ValueError, "entry_mode"):
            validate_source(source)

    def test_scheduled_entry_requires_task_identity(self):
        source = self.base()
        source["formal_entry_source"] = "CHATGPT_SCHEDULED_FORMAL_ANALYSIS"
        source["entry_mode"] = "AUTOMATIC_SCHEDULED_TASK"
        with self.assertRaisesRegex(ValueError, "task_id"):
            validate_source(source)

    def test_missing_business_judgment_fails_closed(self):
        source = self.base()
        source.pop("capital_use")
        with self.assertRaises(ValueError):
            validate_source(source)

    def test_evidence_consumption_missing_fails_closed(self):
        source = self.base()
        source.pop("decision_evidence_consumption")
        with self.assertRaisesRegex(ValueError, "decision_evidence_consumption"):
            validate_source(source)

    def test_evidence_consumption_cross_request_fails_closed(self):
        source = self.base()
        source["parent_request_id"] = "parent-r1"
        with self.assertRaisesRegex(ValueError, "request_id must match parent request"):
            validate_source(source)

    def test_evidence_consumption_incomplete_chain_fails_closed(self):
        source = self.base()
        source["decision_evidence_consumption"]["discovery_to_capital_competition_consumed"] = False
        with self.assertRaisesRegex(ValueError, "discovery_to_capital_competition_consumed"):
            validate_source(source)

    def test_pit_mismatch_fails_closed(self):
        with self.assertRaises(ValueError):
            validate_source(self.base(), expected_snapshot="other")


    def test_durable_source_survives_projection_failure_and_same_source_retry(self):
        """Regression: projection failure cannot erase or regenerate the durable Source."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from scripts import business_decision_source as source_contract
        from scripts.decision_source_persistence import persist_source, fingerprint

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_input = self.base()
            source_input.update({"parent_request_id": "r1", "business_decision": dict(self.base())})
            # The persistence owner performs Phase 1 and writes the immutable Source.
            durable = persist_source(root, source_input)
            self.assertTrue(durable["source_durable"])
            self.assertTrue(durable["reply_ready"])
            source_path = Path(durable["path"])
            before_bytes = source_path.read_bytes()
            before = source_path.read_text(encoding="utf-8")
            before_obj = __import__("json").loads(before)
            before_fp = before_obj["fingerprint"]
            before_identity = (before_obj["request_id"], before_obj.get("decision_id"))

            # Inject failure after durability, at the existing projection function.
            with patch.object(source_contract, "build_formal_completion_from_source", side_effect=RuntimeError("injected projection failure")):
                with self.assertRaisesRegex(RuntimeError, "injected projection failure"):
                    source_contract.build_formal_completion_from_source(before_obj)
            self.assertEqual(source_path.read_bytes(), before_bytes)
            self.assertEqual(source_path.read_text(encoding="utf-8"), before)
            self.assertEqual(fingerprint(before_obj), before_fp)
            self.assertEqual((before_obj["request_id"], before_obj.get("decision_id")), before_identity)

            # Retry consumes the exact durable bytes; it does not rebuild a decision.
            retry_source = __import__("json").loads(source_path.read_text(encoding="utf-8"))
            projected = source_contract.build_formal_completion_from_source(retry_source)
            self.assertEqual(projected["projection_status"], "READY")
            self.assertEqual(projected["decision_id"], before_identity[1])
            self.assertEqual((retry_source["request_id"], retry_source.get("decision_id")), before_identity)
            self.assertEqual(fingerprint(retry_source), before_fp)
            # Idempotent persistence of the same Source must not create another Source.
            second = persist_source(root, retry_source)
            self.assertTrue(second["idempotent_reuse"])
            self.assertEqual(len(list((root / "events" / "decision_sources").glob("*.json"))), 1)

    def test_human_holding_actions_normalize_at_projection_boundary(self):
        from scripts.business_decision_source import _normalize_holding_action

        self.assertEqual(_normalize_holding_action("继续持有，不追加、不减仓"), "HOLD")
        self.assertEqual(_normalize_holding_action("继续持有，不追加、不卖出"), "HOLD")
        self.assertEqual(_normalize_holding_action("降低风险100股"), "REDUCE")
        self.assertEqual(_normalize_holding_action("全部退出"), "EXIT")

    def test_formal_secondary_items_are_split_into_paragraphs(self):
        inline = "（1）第一项内容。 （2）第二项内容。 （3）第三项内容。"
        normalized = _normalize_formal_presentation_layout(inline)
        self.assertEqual(
            normalized,
            "（1）第一项内容。\\n\\n（2）第二项内容。\\n\\n（3）第三项内容。",
        )
        self.assertNotIn("。 （2）", normalized)
        self.assertEqual(
            _normalize_formal_presentation_layout("证券（159781）"),
            "证券（159781）",
        )

    def test_short_but_structurally_valid_presentation_is_not_length_blocked(self):
        content = (
            "【业务结论】\n【数据时点与必要依据】\n【三层监测】\n"
            "【持仓动作】\n【资本用途】\n【全市场机会发现】\n【观察ETF】\n"
            "【关键风险】\n【后续安排】\n【置信度】\n【最可能出错的地方】\n"
            "持有管理；不追加；可释放资本；实际新增部署；Trial；Confirm；1."
        )
        _validate_formal_presentation_contract({
            "presentation_content": content,
            "decision_response": {"presentation_content": content},
            "presentation_binding": {"full_content": content},
        })

    def test_secondary_item_requires_primary_parent(self):
        content = (
            "【业务结论】\n\n1）错误的直属二级项目\n\n"
            "【数据时点与必要依据】\n【三层监测】\n【持仓动作】\n"
            "【资本用途】\n【全市场机会发现】\n【观察ETF】\n"
            "【关键风险】\n【后续安排】\n【置信度】\n【最可能出错的地方】"
        )
        with self.assertRaisesRegex(ValueError, "secondary numbering"):
            _validate_formal_presentation_contract({
                "presentation_content": content,
                "decision_response": {"presentation_content": content},
                "presentation_binding": {"full_content": content},
            })

    def test_projection_is_deterministic_and_does_not_redecide(self):
        projected = build_formal_completion_from_source(self.base())
        self.assertEqual(projected["request_type"], STATE_SYNC_ONLY)
        self.assertEqual(projected["decision_id"], "d1")
        self.assertEqual(projected["next_unit_capital_use"], "现金")
        self.assertEqual(projected["projection_status"], "READY")



    def test_stale_nested_decision_identity_fails_closed(self):
        source = self.base()
        source["parent_request_id"] = "r1"
        source["decision_response"] = {"decision_id": "old-decision"}
        with self.assertRaisesRegex(ValueError, "decision_response.decision_id"):
            validate_source(source)

    def test_stale_request_bound_presentation_timestamp_fails_closed(self):
        source = self.base()
        source.update({
            "request_id": "fd-chatgpt-20261015-083934-k4n8pz",
            "parent_request_id": "fd-chatgpt-20261015-083934-k4n8pz",
            "decision_id": "fd-chatgpt-20261015-083934-k4n8pz_decision",
            "requested_at_beijing": "2026-10-15T08:39:34+08:00",
            "presentation_content": "【ETF正式决策】2026-10-15 08:40 正式判断\\n\\n当前请求于2026-10-15 07:23（北京时间）发起",
            "presentation_binding": {
                "request_id": "fd-chatgpt-20261015-083934-k4n8pz",
                "decision_id": "fd-chatgpt-20261015-083934-k4n8pz_decision",
                "full_content": "【ETF正式决策】2026-10-15 08:40 正式判断\\n\\n当前请求于2026-10-15 07:23（北京时间）发起",
            },
            "decision_response": {
                "decision_id": "fd-chatgpt-20261015-083934-k4n8pz_decision",
                "presentation_content": "【ETF正式决策】2026-10-15 08:40 正式判断\\n\\n当前请求于2026-10-15 07:23（北京时间）发起",
            },
        })
        with self.assertRaisesRegex(ValueError, "current request timestamp"):
            validate_source(source)
\nif __name__ == "__main__":
    unittest.main()
