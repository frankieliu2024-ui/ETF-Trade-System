import unittest

from scripts.formal_entry_contract import (
    ENTRY_AUTOMATIC_SCHEDULED_TASK,
    ENTRY_MANUAL_CONVERSATION,
    ENTRY_MANUAL_SCHEDULED_TASK,
    build_identity,
    validate_formal_entry_identity,
)


class FormalEntryContractTests(unittest.TestCase):
    def base(self, mode, source, occurrence):
        return {
            "entry_mode": mode,
            "source": source,
            "request_id": "fd_test",
            "task_id": "ETF正式决策",
            "task_run_id": "run_test",
            "occurrence": occurrence,
            "market_date": "2026-10-13",
        }

    def test_three_entry_modes_are_distinct(self):
        for mode, source, occurrence in (
            (ENTRY_MANUAL_CONVERSATION, "CHATGPT_MANUAL_FORMAL_ANALYSIS", "manual"),
            (ENTRY_MANUAL_SCHEDULED_TASK, "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS", "manual_task"),
            (ENTRY_AUTOMATIC_SCHEDULED_TASK, "CHATGPT_SCHEDULED_FORMAL_ANALYSIS", "scheduled_20261013_1430"),
        ):
            self.assertEqual(validate_formal_entry_identity(self.base(mode, source, occurrence)), (True, ""))

    def test_noncanonical_task_id_fails_closed(self):
        payload = self.base(
            ENTRY_MANUAL_CONVERSATION,
            "CHATGPT_MANUAL_FORMAL_ANALYSIS",
            "manual",
        )
        payload["task_id"] = "CHATGPT_MANUAL_FORMAL_ANALYSIS_20261016T205101_MANUAL_FORMAL"
        self.assertEqual(validate_formal_entry_identity(payload), (False, "task_id_must_be_ETF正式决策"))

    def test_manual_task_cannot_impersonate_automatic_occurrence(self):
        payload = self.base(
            ENTRY_MANUAL_SCHEDULED_TASK,
            "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            "scheduled_20261013_1430",
        )
        self.assertEqual(validate_formal_entry_identity(payload), (False, "manual_task_must_not_impersonate_automatic_occurrence"))

    def test_dedup_key_is_stable(self):
        identity = build_identity(self.base(
            ENTRY_MANUAL_SCHEDULED_TASK,
            "CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS",
            "manual_task",
        ))
        self.assertEqual(identity.dedup_key, "2026-10-13:ETF正式决策:MANUAL_SCHEDULED_TASK:manual_task")


if __name__ == "__main__":
    unittest.main()
