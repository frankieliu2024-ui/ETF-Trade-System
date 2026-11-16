from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProviderPolicyConsumerContractTests(unittest.TestCase):
    def test_production_consumers_import_canonical_resolver(self):
        cloud = (ROOT / "scripts/cloud_runner_snapshot.py").read_text(encoding="utf-8")
        legacy = (ROOT / "scripts/build_account_stock_market_legacy.py").read_text(encoding="utf-8")
        self.assertIn("resolve_object_policy", cloud)
        self.assertIn("resolve_object_policy", legacy)

    def test_identity_defaults_cover_new_a_share_etf_stock_and_index(self):
        from scripts.provider_policy import resolve_provider_policy
        config = {"object_fallback_policy": {}, "asset_type_provider_defaults": {
            "CN:ETF": {"primary": "tencent_qq", "fallback": ["hithink_finance", "eastmoney_push2"], "direct_only": True},
            "CN:STOCK": {"primary": "tencent_qq", "fallback": ["hithink_finance", "eastmoney_push2"], "direct_only": True},
            "CN:INDEX": {"primary": "tencent_qq", "fallback": ["hithink_finance"], "direct_only": True},
        }}
        self.assertEqual(resolve_provider_policy(config, "159265.SZ", asset_class="ETF", code="159265")["primary"], "tencent_qq")
        self.assertEqual(resolve_provider_policy(config, "300124.SZ", asset_class="STOCK", code="300124")["primary"], "tencent_qq")
        self.assertEqual(resolve_provider_policy(config, "000688.SH", asset_class="INDEX", code="000688")["primary"], "tencent_qq")

    def test_unknown_overseas_identity_does_not_guess_provider(self):
        from scripts.provider_policy import resolve_provider_policy
        self.assertIsNone(resolve_provider_policy({"object_fallback_policy": {}}, "XYZ", asset_class="STOCK", market="US"))

    def test_future_a_share_etf_policy_is_resolved_without_static_entry(self):
        from scripts.provider_policy import resolve_object_policy

        config = {
            "object_fallback_policy": {},
            "default_object_fallback_policy": {
                "primary": "tencent_qq",
                "fallback": ["hithink_finance", "eastmoney_push2"],
                "direct_only": True,
            },
        }
        policy = resolve_object_policy(
            config,
            "512999.SH",
            asset_class="ETF",
            code="512999",
        )
        self.assertEqual(policy["primary"], "tencent_qq")
        self.assertEqual(policy["fallback"], ["hithink_finance", "eastmoney_push2"])
        self.assertTrue(policy["direct_only"])


if __name__ == "__main__":
    unittest.main()
