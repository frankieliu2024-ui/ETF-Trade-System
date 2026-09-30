from pathlib import Path
import unittest


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "market-snapshot.yml"


class FormalReplayEnvScopeTests(unittest.TestCase):
    def test_replay_commits_are_persisted_and_exported_in_same_step(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        persist_source = 'echo "FORMAL_REPLAY_SOURCE_COMMIT=$formal_source_commit" >> "$GITHUB_ENV"'
        persist_dwp = 'echo "FORMAL_REPLAY_DWP_COMMIT=$formal_dwp_commit" >> "$GITHUB_ENV"'
        local_source = 'FORMAL_REPLAY_SOURCE_COMMIT="$formal_source_commit"'
        local_dwp = 'FORMAL_REPLAY_DWP_COMMIT="$formal_dwp_commit"'
        export_line = 'export FORMAL_REPLAY_SOURCE_COMMIT FORMAL_REPLAY_DWP_COMMIT'
        for marker in (persist_source, persist_dwp, local_source, local_dwp, export_line):
            self.assertIn(marker, text)
        block_start = text.index(persist_source)
        block_end = text.index("          else:", block_start)
        block = text[block_start:block_end]
        self.assertLess(block.index(persist_source), block.index(local_source))
        self.assertLess(block.index(persist_dwp), block.index(local_dwp))
        self.assertLess(block.index(local_dwp), block.index(export_line))

    def test_state_sync_uses_the_same_step_variables(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        export_line = 'export FORMAL_REPLAY_SOURCE_COMMIT FORMAL_REPLAY_DWP_COMMIT'
        invocation = 'python scripts/process_state_sync_request.py --formal-replay-source-commit "$FORMAL_REPLAY_SOURCE_COMMIT" --formal-replay-dwp-commit "$FORMAL_REPLAY_DWP_COMMIT" "$request_files"'
        self.assertLess(text.index(export_line), text.index(invocation))


if __name__ == "__main__":
    unittest.main()
