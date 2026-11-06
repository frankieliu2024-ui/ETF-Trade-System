import unittest

from scripts.formal_etf_opportunity_discovery import _management_review_contract


class DiscoveryManagementReviewContractTests(unittest.TestCase):
    def test_holding_requires_direct_hold_reduce_exit_conclusion(self):
        review = _management_review_contract("159217", {"159217"}, {"159217"})
        self.assertEqual(review["object_role"], "HOLDING")
        self.assertEqual(review["conclusion_status"], "PENDING_MASTER_DECISION")
        self.assertEqual(
            review["allowed_conclusions"],
            ["CONTINUE_HOLDING", "REDUCE_REVIEW", "EXIT_REVIEW"],
        )

    def test_new_discovery_requires_direct_discovery_conclusion(self):
        review = _management_review_contract("520550", set(), set())
        self.assertEqual(review["object_role"], "NEW_DISCOVERY")
        self.assertIn("TRIAL_ELIGIBILITY_REVIEW", review["allowed_conclusions"])
        self.assertTrue(review["direct_conclusion_required"])


if __name__ == "__main__":
    unittest.main()
