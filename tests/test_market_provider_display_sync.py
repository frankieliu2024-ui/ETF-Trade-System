from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def canonical_chain(priority: dict, object_id: str) -> list[str]:
    policies = priority.get("object_fallback_policy") or {}
    policy = policies.get(object_id)
    if isinstance(policy, dict) and policy.get("primary"):
        return [str(policy["primary"]), *[str(x) for x in (policy.get("fallback") or [])]]
    return [str(x) for x in ((priority.get("objects") or {}).get(object_id) or [])]


class ProviderPolicyConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.monitor = load_json("config/market/market_monitor_config.json")
        self.priority = load_json("config/market/provider_priority.json")
        self.universe = load_json("config/market/etf_monitor_universe.json")

    def test_object_policy_matches_declared_provider_registry(self):
        """Every policy must use exactly the providers registered for that object.

        `object_fallback_policy` is the sole authority for primary/fallback order.
        `objects` is a provider registry and therefore must match membership, not
        duplicate priority ordering. This prevents missing/extra providers without
        creating a second priority source that can itself drift.
        """
        objects = self.priority.get("objects") or {}
        policies = self.priority.get("object_fallback_policy") or {}
        self.assertGreaterEqual(len(policies), 1, "no object fallback policies configured")

        for object_id, policy in sorted(policies.items()):
            self.assertIn(object_id, objects, f"policy object missing from provider objects: {object_id}")
            self.assertIsInstance(policy, dict, f"invalid provider policy for {object_id}")
            self.assertTrue(policy.get("primary"), f"missing primary provider for {object_id}")
            expected = {str(policy["primary"]), *[str(x) for x in (policy.get("fallback") or [])]}
            actual = {str(x) for x in (objects.get(object_id) or [])}
            self.assertEqual(actual, expected, f"provider registry drift for {object_id}")

    def test_formal_index_display_matches_canonical_provider_chain(self):
        """Display order follows authoritative policy; registry-only objects check membership only."""
        display_objects = {
            str(item.get("id")): item
            for item in ((((self.monitor.get("classes") or {}).get("A") or {}).get("objects")) or [])
            if isinstance(item, dict) and item.get("id")
        }
        monitor_formal = set((self.monitor.get("formal_index_layer") or {}).get("required_objects") or [])
        priority_formal = set(self.priority.get("formal_index_objects") or [])
        self.assertEqual(monitor_formal, priority_formal, "formal index universe drift")

        objects = self.priority.get("objects") or {}
        policies = self.priority.get("object_fallback_policy") or {}
        for object_id in sorted(priority_formal):
            self.assertIn(object_id, display_objects, f"missing formal-index display object: {object_id}")
            registered = [str(x) for x in (objects.get(object_id) or [])]
            self.assertGreaterEqual(len(registered), 1, f"formal index has no registered provider: {object_id}")
            display = display_objects[object_id]
            preferred = str(display.get("preferred_source") or "")
            actual_fallback = [x for x in str(display.get("fallback_source") or "").split("|") if x]
            policy = policies.get(object_id)

            if isinstance(policy, dict) and policy.get("primary"):
                chain = [str(policy["primary"]), *[str(x) for x in (policy.get("fallback") or [])]]
                self.assertEqual(preferred, chain[0], f"preferred_source drift for {object_id}")
                self.assertEqual(actual_fallback, chain[1:], f"fallback_source drift for {object_id}")
            else:
                self.assertIn(preferred, registered, f"display primary is not registered for {object_id}")
                for source in actual_fallback:
                    self.assertIn(source, registered, f"display fallback is not registered for {object_id}: {source}")

    def test_monitoring_index_universe_matches_formal_index_universe(self):
        index_monitor = (((self.monitor.get("monitoring_layers") or {}).get("index_monitor")) or {})
        self.assertEqual(
            set(index_monitor.get("required_objects") or []),
            set(self.priority.get("formal_index_objects") or []),
            "index monitoring universe drift",
        )
        objects = self.priority.get("objects") or {}
        for object_id in index_monitor.get("conditional_objects") or []:
            self.assertIn(object_id, objects, f"conditional index object has no provider declaration: {object_id}")
            self.assertGreaterEqual(len(objects.get(object_id) or []), 1, f"conditional index has empty provider chain: {object_id}")

    def test_etf_universe_has_complete_canonical_provider_policy(self):
        """Every continuously monitored ETF must have one complete canonical production chain."""
        objects = self.priority.get("objects") or {}
        policies = self.priority.get("object_fallback_policy") or {}
        etfs = self.universe.get("objects") or []
        self.assertGreaterEqual(len(etfs), 1, "canonical ETF universe is empty")

        for item in etfs:
            thscode = str(item.get("thscode") or "")
            self.assertTrue(thscode, "ETF universe contains empty thscode")
            self.assertIn(thscode, objects, f"ETF missing provider declaration: {thscode}")
            self.assertIn(thscode, policies, f"ETF missing canonical fallback policy: {thscode}")
            self.assertGreaterEqual(len(canonical_chain(self.priority, thscode)), 1, f"ETF has empty provider chain: {thscode}")

    def test_production_scope_declarations_do_not_drift(self):
        """Production scope labels duplicated for display must match the provider authority exactly."""
        monitor_scopes = set((self.monitor.get("provider_expansion") or {}).get("tencent_current_production") or [])
        priority_scopes = set((self.priority.get("tencent_expansion_policy") or {}).get("production_enabled") or [])
        self.assertEqual(monitor_scopes, priority_scopes, "Tencent production scope drift")

        # Query-time A-share industry stocks are a formal dynamic scope and must
        # retain an explicit production policy. Candidate/shadow overseas scopes
        # are intentionally not hardened here.
        dynamic = self.priority.get("dynamic_scope_policy") or {}
        query_scope = dynamic.get("A_SHARE_QUERY_TIME_INDUSTRY_STOCK") or {}
        self.assertEqual(str(query_scope.get("primary") or ""), "tencent_qq")
        self.assertGreaterEqual(len(query_scope.get("fallback") or []), 1, "query-time A-share stock scope has no fallback")
        self.assertTrue(query_scope.get("direct_only") is True, "query-time A-share stock scope must remain direct-only")

    def test_us_extended_hours_formal_proxies_have_provider_declarations(self):
        """Every configured base proxy for US extended hours must have an explicit formal source chain."""
        extended = self.priority.get("us_extended_hours") or {}
        objects = self.priority.get("objects") or {}
        base_proxies = [str(x) for x in (extended.get("base_proxies") or [])]
        self.assertGreaterEqual(len(base_proxies), 1, "US extended-hours base proxies are empty")
        for proxy in base_proxies:
            object_id = f"{proxy}_EXTENDED"
            self.assertIn(object_id, objects, f"US extended-hours proxy missing provider declaration: {object_id}")
            chain = [str(x) for x in (objects.get(object_id) or [])]
            self.assertGreaterEqual(len(chain), 1, f"US extended-hours proxy has empty provider chain: {object_id}")
            self.assertTrue(
                chain[0].startswith("yahoo_chart_api") and "includePrePost=true" in chain[0],
                f"US extended-hours primary drift for {object_id}: {chain[0]}",
            )

    def test_normative_domain_sources_and_machine_fact_boundary(self):
        """Governance must reduce ambiguity without turning dynamic facts into Markdown authority."""
        data_spec = (ROOT / "ETF与市场监测数据接口使用规范.md").read_text(encoding="utf-8")
        mutation_spec = (ROOT / "docs/生产变更与并发写入协议_V1.0.md").read_text(encoding="utf-8")
        notification_spec = (ROOT / "docs/ETF主动通知体系.md").read_text(encoding="utf-8")
        query_note = (ROOT / "docs/市场行情查询路由与全球时点规则_V1.0.md").read_text(encoding="utf-8")
        index = (ROOT / "ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")

        self.assertIn("数据与市场监测域的唯一规范性规则来源", data_spec)
        self.assertIn("规则语义与动态运行事实严格分离", data_spec)
        self.assertIn("config/market/provider_priority.json", data_spec)
        self.assertIn("config/market/etf_monitor_universe.json", data_spec)
        self.assertIn("config/runtime_policy.json", data_spec)
        self.assertIn("生产变更、状态写入与并发治理域的唯一规范性规则来源", mutation_spec)
        self.assertIn("运行事实以当前 `main` 的实际workflow、脚本和状态为准", mutation_spec)
        self.assertIn("唯一规范性规则来源", notification_spec)
        self.assertNotIn("唯一规范性规则来源", query_note)

        for path in (
            "ETF规则_MASTER.md",
            "ETF与市场监测数据接口使用规范.md",
            "docs/ETF主动通知体系.md",
            "docs/生产变更与并发写入协议_V1.0.md",
        ):
            self.assertIn(path, index, f"system index must route normative domain: {path}")
        self.assertIn("本索引只负责路由", index)
        self.assertIn("不再新增新的“唯一规范性规则来源”", index)


if __name__ == "__main__":
    unittest.main()