from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProviderPolicyConsumerContractTests(unittest.TestCase):
    def test_production_consumers_import_canonical_resolver(self):
        cloud = (ROOT / "scripts/cloud_runner_snapshot.py").read_text(encoding="utf-8")
        legacy = (ROOT / "scripts/build_account_stock_market_legacy.py").read_text(encoding="utf-8")
        self.assertIn("resolve_object_policy", cloud)
        self.assertIn("resolve_object_policy", legacy)

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
