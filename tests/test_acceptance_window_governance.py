import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    "self-healing-watchdog.yml",
    "market-snapshot.yml",
    "decision-notification.yml",
    "research-backfill.yml",
    "base-stock-screen.yml",
    "historical-canonical-backfill.yml",
    "on-demand-market-data.yml",
    "overseas-preopen-pulse.yml",
    "us-extended-hours-pulse.yml",
    "opening-auction-current-fallback.yml",
    "workflow-failure-guard.yml",
    "system-consistency.yml",
    "formal-decision-e2e-acceptance.yml",
)


class AcceptanceWindowGovernanceTests(unittest.TestCase):
    def test_all_canonical_writers_share_acceptance_lane(self):
        for name in WORKFLOWS:
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^  group: etf-main-canonical-writer\s*$", name)

    def test_acceptance_evidence_contains_immutable_window_identity(self):
        for name in ("system-consistency.yml", "formal-decision-e2e-acceptance.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for field in (
                "input_sha",
                "final_main_sha",
                "acceptance_sha",
                "sha_stable",
                "state_write_commits",
                "acceptance_window_id",
                "writer_runs_during_window",
                "blocked_or_isolated_writers",
            ):
                self.assertIn(field, text, f"{name}: missing {field}")

    def test_pr_e2e_checks_current_head_and_writer_token_contract(self):
        e2e = (ROOT / ".github" / "workflows" / "formal-decision-e2e-acceptance.yml").read_text(encoding="utf-8")
        self.assertIn("github.event.pull_request.head.sha", e2e)
        self.assertIn("Verify acceptance source SHA", e2e)
        for name in ("market-snapshot.yml", "opening-auction-current-fallback.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("group: etf-main-canonical-writer", text)
            self.assertNotIn("etf-main-canonical-writer", text)
        protocol = (ROOT / "config" / "maintenance" / "production_mutation_protocol.json").read_text(encoding="utf-8")
        self.assertIn("etf-main-canonical-writer", protocol)
        self.assertNotIn("etf-main-canonical-writer", protocol)

    def test_consistency_acceptance_never_publishes_to_main(self):
        text = (ROOT / ".github" / "workflows" / "system-consistency.yml").read_text(encoding="utf-8")
        self.assertNotIn("git push origin HEAD:main", text)


if __name__ == "__main__":
    unittest.main()
