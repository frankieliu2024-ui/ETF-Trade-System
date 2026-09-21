import unittest

from scripts.check_system_consistency_core import workflow_sha_validation


class WorkflowShaSnapshotTests(unittest.TestCase):
    def test_production_run_requires_exact_workflow_sha(self):
        self.assertEqual(
            workflow_sha_validation("abc", "abc", "workflow_dispatch"),
            (True, "HEAD=abc GITHUB_SHA=abc"),
        )
        self.assertEqual(
            workflow_sha_validation("abc", "def", "push"),
            (False, "HEAD=abc GITHUB_SHA=def"),
        )

    def test_production_acceptance_allows_proven_runtime_only_head_advance(self):
        semantic = {
            "base": "stable-anchor",
            "head": "runtime-head",
            "decision": "SEMANTICALLY_FRESH",
        }
        ok, detail = workflow_sha_validation(
            "runtime-head",
            "workflow-sha",
            "push",
            stable_acceptance_base="stable-anchor",
            semantic_freshness=semantic,
        )
        self.assertTrue(ok)
        self.assertIn("runtime-only latest-main advancement validated", detail)

    def test_production_acceptance_rejects_unproven_or_replay_required_head_advance(self):
        replay = {
            "base": "stable-anchor",
            "head": "runtime-head",
            "decision": "REPLAY_REQUIRED",
        }
        self.assertFalse(
            workflow_sha_validation(
                "runtime-head",
                "workflow-sha",
                "push",
                stable_acceptance_base="stable-anchor",
                semantic_freshness=replay,
            )[0]
        )
        self.assertFalse(
            workflow_sha_validation(
                "runtime-head",
                "workflow-sha",
                "push",
                stable_acceptance_base="wrong-anchor",
                semantic_freshness={
                    "base": "stable-anchor",
                    "head": "runtime-head",
                    "decision": "SEMANTICALLY_FRESH",
                },
            )[0]
        )

    def test_pull_request_replay_accepts_ephemeral_candidate_head(self):
        ok, detail = workflow_sha_validation("candidate-head", "merge-event-sha", "pull_request")
        self.assertTrue(ok)
        self.assertIn("PR candidate snapshot validated", detail)
        self.assertIn("candidate-head", detail)
        self.assertIn("merge-event-sha", detail)

    def test_local_runs_without_workflow_sha_are_explicitly_unbound(self):
        self.assertEqual(
            workflow_sha_validation("local-head", "", ""),
            (True, "workflow_sha_unset event=UNKNOWN"),
        )


if __name__ == "__main__":
    unittest.main()
