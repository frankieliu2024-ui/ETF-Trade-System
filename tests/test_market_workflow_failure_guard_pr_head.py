from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/workflow-failure-guard.yml"


class WorkflowFailureGuardPrHeadTests(unittest.TestCase):
    def test_non_main_head_is_fetched_and_verified_before_parent_diff(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        fetch_marker = 'git fetch --no-tags --depth=2 origin "$HEAD_SHA" --quiet 2>/dev/null || true'
        head_guard = 'git cat-file -e "${HEAD_SHA}^{commit}"'
        parent_verify = 'git rev-parse --verify "$HEAD_SHA^"'
        parent_guard = 'git cat-file -e "${PARENT_SHA}^{commit}"'
        diff_marker = 'git diff --name-only "$PARENT_SHA" "$HEAD_SHA"'
        self.assertIn(fetch_marker, text)
        self.assertIn(head_guard, text)
        self.assertIn(parent_verify, text)
        self.assertIn(parent_guard, text)
        self.assertIn(diff_marker, text)
        self.assertLess(text.index(fetch_marker), text.index(parent_verify))
        self.assertLess(text.index(head_guard), text.index(parent_verify))
        self.assertLess(text.index(parent_guard), text.index(diff_marker))

    def test_normalization_uses_verified_commit_and_preserves_packet_fallback(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ok('git', 'cat-file', '-e', f'{head}^{{commit}}')", text)
        self.assertIn("out('git', 'rev-parse', '--verify', f'{head}^')", text)
        self.assertIn("or data.get('commit_message')", text)
        self.assertIn("or data.get('commit_author')", text)


if __name__ == "__main__":
    unittest.main()
