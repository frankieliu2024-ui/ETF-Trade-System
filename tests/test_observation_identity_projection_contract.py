from pathlib import Path
import json
import unittest


class ObservationIdentityProjectionContractTests(unittest.TestCase):
    def test_monitor_universe_has_explicit_identity_roles(self):
        universe = json.loads(Path("config/market/etf_monitor_universe.json").read_text(encoding="utf-8"))
        roles = {str(item.get("code")): item.get("identity_role") for item in universe["objects"]}
        self.assertEqual(roles["159941"], "HOLDING")
        self.assertEqual(roles["159981"], "HOLDING")
        self.assertNotIn("159529", roles)
        self.assertNotIn("159992", roles)
        self.assertEqual(roles["588000"], "OBSERVATION")

    def test_projection_and_dashboard_require_explicit_observation_role(self):
        source = Path("scripts/observation_etf_management.py").read_text(encoding="utf-8")
        dashboard = Path("scripts/sync_formal_files.py").read_text(encoding="utf-8")
        account_sync = Path("scripts/process_state_sync_request.py").read_text(encoding="utf-8")
        self.assertIn('"identity_role": "HOLDING"', source)
        self.assertIn('"identity_role": "OBSERVATION"', source)
        self.assertIn("Remove legacy holding entries after the account confirms quantity zero.", source)
        self.assertIn('identity_role") or "").upper() == "OBSERVATION"', dashboard)
        self.assertIn('identity_role") or "").upper() == "OBSERVATION"', account_sync)


if __name__ == "__main__":
    unittest.main()
