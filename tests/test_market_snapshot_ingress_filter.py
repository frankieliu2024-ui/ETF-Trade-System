from pathlib import Path
import unittest


class MarketSnapshotIngressFilterTests(unittest.TestCase):
  def test_downstream_decision_artifacts_are_not_snapshot_ingress(self):
    workflow = Path(".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    assert "__business_decision_source" not in workflow
    assert "__formal_completion" not in workflow
    assert "grep -vE" not in workflow
