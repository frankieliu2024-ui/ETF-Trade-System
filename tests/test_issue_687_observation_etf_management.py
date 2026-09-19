from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.observation_etf_management import project_monitor_universe, validate_observation_management


def root_fixture() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "config/market").mkdir(parents=True)
    (root / "config/market/etf_monitor_universe.json").write_text(json.dumps({
        "version": "V1.1", "mode": "HOLDING_PLUS_ADMITTED_OBSERVATION",
        "objects": [
            {"code":"588000","name":"科创50ETF","thscode":"588000.SH"},
            {"code":"513180","name":"恒生科技ETF","thscode":"513180.SH"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return root


class ObservationEtfManagementTest(unittest.TestCase):
    def test_discovery_hit_alone_cannot_admit_observation(self) -> None:
        with self.assertRaises(ValueError):
            validate_observation_management(
                {"observation_management":[{"action":"ADMIT","code":"510300","name":"300ETF","thscode":"510300.SH"}]},
                held_codes=set(), monitored_codes={"588000","513180"},
            )

    def test_admission_requires_live_falsifiable_thesis(self) -> None:
        out = validate_observation_management(
            {"observation_management":[{
                "action":"ADMIT","code":"510300","name":"300ETF","thscode":"510300.SH",
                "thesis":"宽基风险偏好修复可能延续",
                "falsifier":"指数与ETF自身结构同步转弱",
                "next_decision_information":"下一正式节点承接与相对反馈",
                "information_value_reason":"可能改变Trial/Confirm及资本配置",
            }]},
            held_codes=set(), monitored_codes={"588000","513180"},
        )
        self.assertEqual(out[0]["action"], "ADMIT")

    def test_held_etf_cannot_be_removed_from_continuous_monitor(self) -> None:
        with self.assertRaises(ValueError):
            validate_observation_management(
                {"observation_management":[{"action":"EXIT","code":"588000","reason":"观察假设失效"}]},
                held_codes={"588000"}, monitored_codes={"588000","513180"},
            )

    def test_exit_is_identity_change_not_trade_action(self) -> None:
        root = root_fixture()
        account = {"positions":[]}
        projected, changed = project_monitor_universe(root, account, {
            "observation_management":[{"action":"EXIT","code":"513180","reason":"假设失效且后续信息价值不足"}]
        })
        self.assertTrue(changed)
        self.assertEqual([x["code"] for x in projected["objects"]], ["588000"])

    def test_full_exit_does_not_automatically_create_observation(self) -> None:
        root = root_fixture()
        # No observation-management ADMIT intent: projection is unchanged.
        projected, changed = project_monitor_universe(root, {"positions":[]}, {})
        self.assertFalse(changed)
        self.assertEqual({x["code"] for x in projected["objects"]}, {"588000","513180"})


if __name__ == "__main__":
    unittest.main()
