import unittest
from pathlib import Path

from scripts.validate_workflow_registry import validate_workflow


class WorkflowRegistryGuardTests(unittest.TestCase):
    def test_all_workflows_pass_registry_guard(self):
        root = Path(__file__).resolve().parents[1]
        paths = sorted((root / ".github" / "workflows").glob("*.yml"))
        paths += sorted((root / ".github" / "workflows").glob("*.yaml"))
        self.assertTrue(paths)
        errors = [error for path in paths for error in validate_workflow(path)]
        self.assertEqual(errors, [], "\n".join(errors))

    def test_rejects_unindented_heredoc(self):
        path = Path(__file__).resolve().parent / "_workflow_registry_guard_fixture.yml"
        path.write_text(
            "name: fixture\non:\n  workflow_dispatch:\njobs:\n  test:\n"
            "    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
            "          python - <<'PY'\n"
            "print('bad')\n"
            "          PY\n",
            encoding="utf-8",
        )
        try:
            errors = validate_workflow(path)
            self.assertTrue(any("less indented" in error for error in errors))
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
