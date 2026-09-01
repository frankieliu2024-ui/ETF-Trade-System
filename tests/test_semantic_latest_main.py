import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.semantic_latest_main import classify_delta, classify_path


class SemanticLatestMainTests(unittest.TestCase):
    def test_path_ownership_comes_from_existing_domain_boundaries(self):
        self.assertEqual(classify_path("data/state/CURRENT.json"), "DYNAMIC_RUNTIME_FACT")
        self.assertEqual(classify_path("requests/live_snapshot/x.json"), "REQUEST_OR_TRIGGER_FACT")
        self.assertEqual(classify_path("scripts/build_query_context.py"), "STABLE_PRODUCTION_CHANGE")
        self.assertEqual(classify_path("ETF当前状态_DASHBOARD.md"), "FORMAL_FACT_MUTATION")
        self.assertEqual(classify_path("ETF交易复盘与经验库_2026.md"), "FORMAL_FACT_MUTATION")

    def _repo(self, files_by_commit):
        root = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
        commits = []
        for files in files_by_commit:
            for path, content in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            commits.append(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip())
        return root, commits

    def test_dynamic_only_main_movement_is_semantically_fresh(self):
        root, commits = self._repo([{"data/state/CURRENT.json": "old"}, {"data/state/CURRENT.json": "new"}])
        result = classify_delta(root, commits[0], commits[1])
        self.assertEqual(result["decision"], "SEMANTICALLY_FRESH")

    def test_stable_change_requires_replay(self):
        root, commits = self._repo([{"data/state/CURRENT.json": "old"}, {"scripts/x.py": "source"}])
        self.assertEqual(classify_delta(root, commits[0], commits[1])["decision"], "REPLAY_REQUIRED")

    def test_unknown_change_requires_replay(self):
        root, commits = self._repo([{"data/state/CURRENT.json": "old"}, {"new.bin": "unknown"}])
        self.assertEqual(classify_delta(root, commits[0], commits[1])["decision"], "REPLAY_REQUIRED")

    def test_unregistered_json_change_is_not_assumed_dynamic_or_stable(self):
        root, commits = self._repo([{"data/state/CURRENT.json": "old"}, {"unregistered.json": "unknown"}])
        self.assertEqual(classify_delta(root, commits[0], commits[1])["decision"], "REPLAY_REQUIRED")

    def test_formal_or_request_movement_requires_review_but_not_automatic_replay(self):
        root, commits = self._repo([{"data/state/CURRENT.json": "old"}, {"events/research/x.json": "fact", "requests/live_snapshot/x.json": "request"}])
        self.assertEqual(classify_delta(root, commits[0], commits[1])["decision"], "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()

