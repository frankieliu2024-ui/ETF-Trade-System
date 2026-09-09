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
