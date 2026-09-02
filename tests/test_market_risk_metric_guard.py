import json
import tempfile
import unittest
from pathlib import Path

from scripts.state_manager import build_etf_strategy_risk_metrics
from scripts.build_e2e_status import risk_component
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_formal_review_risk(root: Path) -> tuple[float, str]:
    candidates = []
    review_dir = root / "events" / "reviews"
    for path in review_dir.glob("*.json") if review_dir.exists() else []:
        event = read_json(path)
        review = event.get("review") or event.get("formal_review") or {}
        fact = review.get("etf_strategy_known_net") or {}
        try:
            risk = float(fact.get("etf_strategy_risk_rate_pct"))
        except (TypeError, ValueError):
            continue
        updated = str(event.get("updated_at_beijing") or event.get("account_updated_at") or "")
        if not updated:
            continue
        candidates.append((updated, risk, str(path.relative_to(root)).replace("\\", "/")))
    if not candidates:
        raise AssertionError("no formal ETF strategy risk review available")
    _, risk, source = max(candidates, key=lambda item: item[0])
    return risk, source


class TestMarketRiskMetricGuard(unittest.TestCase):
    def test_formal_review_overrides_stale_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "state").mkdir(parents=True)
            (root / "events" / "reviews").mkdir(parents=True)
            (root / "data" / "state" / "account_fact.json").write_text(
                json.dumps({
                    "status": "VALID",
                    "updated_at": "2026-08-28T10:31:00+08:00",
                    "source": "TEST",
                    "positions": [],
                }),
                encoding="utf-8",
            )
            (root / "data" / "state" / "etf_strategy_equity.json").write_text(
                json.dumps({
                    "generated_at": "2026-08-28T12:00:00+08:00",
                    "summary": {
                        "starting_etf_strategy_capital": 200000,
                        "known_net_current_strategy_return_pct": -8.30,
                        "known_net_current_strategy_equity": 183400,
                    },
                }),
                encoding="utf-8",
            )
            (root / "ETF当前状态_DASHBOARD.md").write_text(
                "> 更新时间：2026-08-28 11:30\n|ETF策略风险率|约-6.18%|\n",
                encoding="utf-8",
            )
            (root / "events" / "reviews" / "2026-08-28.json").write_text(
                json.dumps({
                    "updated_at_beijing": "2026-08-28T11:30:00+08:00",
                    "market_date": "2026-08-28",
                    "review": {
                        "etf_strategy_known_net": {
                            "etf_strategy_risk_rate_pct": -6.18,
                            "known_net_strategy_equity": 187640,
                        }
                    },
                }),
                encoding="utf-8",
            )

            result = build_etf_strategy_risk_metrics(root)
            self.assertEqual(result["etf_strategy_risk_pct"], -6.18)
            self.assertEqual(result["reconstruction_risk_pct"], -8.30)
            self.assertIn("events/reviews/2026-08-28.json", result["risk_source"])
            self.assertIn("FORMAL_REVIEW_PRIMARY", result["equity_data_quality"])

    def test_dashboard_cannot_fallback_when_no_formal_review_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "CURRENT.json").write_text(
                json.dumps({"market_date": "2026-08-28"}), encoding="utf-8"
            )
            (root / "data" / "state" / "account_fact.json").write_text(
                json.dumps({"status": "VALID", "positions": []}), encoding="utf-8"
            )
            (root / "data" / "state" / "etf_strategy_equity.json").write_text(
                json.dumps({
                    "generated_at": "2026-08-28T12:00:00+08:00",
                    "as_of_transaction_date": "2026-08-27",
                    "summary": {
                        "starting_etf_strategy_capital": 200000,
                        "known_net_current_strategy_return_pct": -8.30,
                    },
                }), encoding="utf-8"
            )
            (root / "ETF当前状态_DASHBOARD.md").write_text(
                "> 更新时间：2026-08-28 11:30\\n|ETF策略风险率|约-6.18%|\\n",
                encoding="utf-8",
            )

            result = build_etf_strategy_risk_metrics(root)
            self.assertIsNone(result["etf_strategy_risk_pct"])
            self.assertNotEqual(result["risk_source"], "ETF当前状态_DASHBOARD.md")
            self.assertEqual(result["reconstruction_fresh_for_market_date"], False)

    def test_fresh_reconstruction_is_allowed_without_formal_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "state").mkdir(parents=True)
            (root / "data" / "state" / "CURRENT.json").write_text(
                json.dumps({"market_date": "2026-08-28"}), encoding="utf-8"
            )
            (root / "data" / "state" / "account_fact.json").write_text(
                json.dumps({"status": "VALID", "positions": []}), encoding="utf-8"
            )
            (root / "data" / "state" / "etf_strategy_equity.json").write_text(
                json.dumps({
                    "generated_at": "2026-08-28T12:00:00+08:00",
                    "as_of_transaction_date": "2026-08-28",
                    "summary": {"known_net_current_strategy_return_pct": -8.30},
                }), encoding="utf-8"
            )
            (root / "ETF当前状态_DASHBOARD.md").write_text(
                "|ETF策略风险率|约-6.18%|\\n", encoding="utf-8"
            )

            result = build_etf_strategy_risk_metrics(root)
            self.assertEqual(result["etf_strategy_risk_pct"], -8.30)
            self.assertEqual(result["risk_source"], "data/state/etf_strategy_equity.json")

    def test_e2e_risk_ignores_dashboard_when_reconstruction_stale(self):
        equity = {
            "generated_at": "2026-08-28T12:00:00+08:00",
            "as_of_transaction_date": "2026-08-27",
            "summary": {"known_net_current_strategy_return_pct": -8.30},
        }
        with patch("scripts.build_e2e_status.latest_formal_review_risk", return_value={}):
            result = risk_component(equity, {"market_date": "2026-08-28"})
        self.assertEqual(result["status"], "DEGRADED")
        self.assertIsNone(result["etf_strategy_risk_pct"])
        self.assertNotEqual(result["source"], "ETF当前状态_DASHBOARD.md")

    def test_e2e_risk_blocks_when_all_machine_sources_missing(self):
        with patch("scripts.build_e2e_status.latest_formal_review_risk", return_value={}):
            result = risk_component({"summary": {}}, {"market_date": "2026-08-28"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["etf_strategy_risk_pct"])

    def test_live_decision_and_query_context_match_latest_formal_risk(self):
        formal_risk, formal_source = latest_formal_review_risk(REPO_ROOT)
        decision = read_json(REPO_ROOT / "data" / "state" / "decision_context.json")
        query = read_json(REPO_ROOT / "data" / "state" / "query_context.json")

        decision_metric = decision.get("etf_strategy_risk_metrics") or {}
        query_decision = query.get("decision_context") or {}
        query_metric = query_decision.get("etf_strategy_risk_metrics") or {}

        self.assertIsNotNone(decision_metric.get("etf_strategy_risk_pct"))
        self.assertIsNotNone(query_metric.get("etf_strategy_risk_pct"))
        self.assertAlmostEqual(float(decision_metric["etf_strategy_risk_pct"]), formal_risk, places=2)
        self.assertAlmostEqual(float(query_metric["etf_strategy_risk_pct"]), formal_risk, places=2)
        self.assertEqual(decision_metric.get("risk_source"), formal_source)
        self.assertEqual(query_metric.get("risk_source"), formal_source)
        self.assertNotEqual(
            decision_metric.get("risk_source"),
            "data/state/etf_strategy_equity.json",
            "historical reconstruction must not silently become the formal risk source while a formal review exists",
        )


if __name__ == "__main__":
    unittest.main()
