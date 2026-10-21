import unittest

from scripts.process_state_sync_request import (\n    normalize_broker_account_fact_ingress,\n    validate_broker_account_fact_ingress,\n)


class BrokerAccountFactIngressContractTests(unittest.TestCase):
    def request(self):
        return {
            "source": "CHATGPT_USER_BROKER_SCREENSHOT",
            "request_type": "STATE_SYNC_ONLY",
        }

    def test_rejects_position_arrays_before_account_merge(self):
        error = validate_broker_account_fact_ingress(
            self.request(),
            {"positions": [["300750", 200, 346.908, 297.75, 59550.0]]},
        )
        self.assertIn("positions[0] must be an object", error)

    def test_rejects_name_only_or_invalid_code_identity(self):
        error = validate_broker_account_fact_ingress(
            self.request(),
            {"positions": [{"name": "宁德时代", "quantity": 200}]},
        )
        self.assertIn("six-digit security code", error)

    def test_rejects_duplicate_security_codes(self):
        error = validate_broker_account_fact_ingress(
            self.request(),
            {"positions": [
                {"code": "159941", "quantity": 12200},
                {"code": "159941", "quantity": 12200},
            ]},
        )
        self.assertIn("duplicate code 159941", error)

    def test_accepts_minimal_typed_positions_without_guessing_names(self):
        error = validate_broker_account_fact_ingress(
            self.request(),
            {"positions": [
                {"code": "300750", "quantity": 200, "asset_type": "STOCK"},
                {"code": "159941", "quantity": 12200, "asset_type": "ETF"},
            ]},
        )
        self.assertEqual(error, "")

    def test_moves_explicit_non_security_asset_without_inventing_identity(self):
        request = self.request()
        supplied = {
            "positions": [
                {"name": "纳指ETF", "code": "159941", "quantity": 12200, "asset_type": "ETF"},
                {"name": "华纬发债", "code": None, "quantity": 10, "asset_type": "BOND"},
            ]
        }
        normalized, moved = normalize_broker_account_fact_ingress(request, supplied)
        self.assertEqual(moved, 1)
        self.assertEqual([p["code"] for p in normalized["positions"]], ["159941"])
        self.assertEqual(normalized["unresolved_assets"][0]["name"], "华纬发债")
        self.assertEqual(normalized["unresolved_assets"][0]["identity_status"], "UNRESOLVED_NON_SECURITY_ASSET")
        self.assertEqual(validate_broker_account_fact_ingress(request, normalized), "")

    def test_non_broker_requests_are_not_reclassified_by_this_contract(self):
        self.assertEqual(
            validate_broker_account_fact_ingress(
                {"source": "OTHER"},
                {"positions": [["legacy", "shape"]]},
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
