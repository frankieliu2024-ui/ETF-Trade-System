from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/system-consistency.yml"


class SystemConsistencyCandidatePathTests(unittest.TestCase):
    def test_pr_number_resolution_is_inside_pull_request_guard(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        guard = 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then'
        pr_number = 'pr_number="$(python -c'
        self.assertIn(guard, text)
        self.assertIn(pr_number, text)
        self.assertLess(text.index(guard), text.index(pr_number))
        self.assertNotIn('candidate_root="$RUNNER_TEMP/etf-candidate-$(python', text)

    def test_non_pr_events_default_to_current_checkout(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('validation_root="$PWD"', text)
        guard = 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then'
        self.assertLess(text.index('validation_root="$PWD"'), text.index(guard))
        self.assertIn('candidate_root="$RUNNER_TEMP/etf-candidate-__PR_NUMBER__"', text.replace('${pr_number}', '__PR_NUMBER__'))

    def test_missing_pr_candidate_falls_back_without_cross_pr_reuse(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('if [ -d "$candidate_root" ]; then', text)
        self.assertNotIn('else\n            validation_root="$PWD"', text)

    def test_research_integration_receives_the_same_fresh_report_path(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        research = 'python scripts/check_research_integration.py'
        handoff = 'export ETF_CONSISTENCY_REPORT_PATH="$RUNNER_TEMP/system_consistency.json"'
        self.assertGreaterEqual(text.count(handoff), 2)
        self.assertLess(text.index(handoff), text.index(research))
        research_block = text[text.index(research) - 500:text.index(research) + len(research)]
        self.assertIn(handoff, research_block)


if __name__ == "__main__":
    unittest.main()
