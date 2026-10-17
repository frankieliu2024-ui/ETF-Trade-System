# Legacy / compatibility 路径可达性清单（2026-10-17）

本文是 Issue #1447 的审计证据，不新增业务规则。正式规则和生产治理仍按 `ETF_SYSTEM_INDEX.md` 路由读取。

## 结论

current main 中的部分 `*_legacy.py` 并非死代码或仅恢复代码，而是由 canonical facade 在生产入口中 import、re-export 并调用。当前不存在“第二个同名 workflow 入口”的直接证据，但存在“canonical 入口 → legacy 实现”的生产可达链。该状态不满足“legacy 路径必须明确不可进入生产，或已迁移到统一 owner”的完整要求。

## 已确认的生产可达链

| canonical 入口 | legacy 实现 | 生产调用证据 | 当前判定 | 风险 |
|---|---|---|---|---|
| `scripts/build_account_stock_market.py` | `build_account_stock_market_legacy.py` | `.github/workflows/market-snapshot.yml` 第997行；`.github/workflows/opening-auction-current-fallback.yml` 第101行直接运行 canonical facade | 生产可达 | 对象策略、行情质量和账户市场投影的核心逻辑仍由 legacy 模块持有 |
| `scripts/query_time_market_refresh.py` | `query_time_market_refresh_legacy.py` | `market-snapshot.yml` 将 canonical 文件列入 push 触发；canonical facade import legacy 并调用 `_legacy._a_share` | 生产可达，需继续回读具体调用段 | 查询补采可能沿旧 resolver/质量语义 |
| `scripts/build_market_regime_context.py` | `build_market_regime_context_legacy.py` | canonical facade import `_legacy_build` 并在派生上下文构建中调用 | 派生生产可达 | 市场结构上下文的旧逻辑可能绕过统一事实准入 |
| `scripts/build_market_structure_context.py` | `build_market_structure_context_legacy.py` | canonical facade import `_legacy_build` 并调用 | 派生生产可达 | 同上 |
| `scripts/send_market_shock_notification.py` | `send_market_shock_notification_legacy.py` | `run_guarded_notification.py` 通过 canonical notification entry；canonical facade re-export legacy builders | 通知生产可达 | 通知事件构造和正文/去重语义可能仍由 legacy 实现控制 |

## 非生产引用

以下引用目前仅作为测试或静态合同证据，不能单独证明生产可达：

- `tests/test_dashboard_account_projection.py`
- `tests/test_provider_policy_consumers.py`
- `tests/test_market_notification_materiality_guard.py`

## 当前缺口

1. canonical facade 与 legacy implementation 的 writer、resolver、PIT/freshness 和 degradation 边界尚未拆开验证。
2. 现有 consistency 只检查 canonical entry 的部分静态锚点，不能证明 legacy 实现没有绕过统一准入。
3. 尚未有“禁止 workflow/production caller 直接 import legacy implementation”的回归测试。
4. 尚未完成把 legacy 实现迁移到唯一 owner，或明确将其降级为 recovery-only 的代码变更。

## 下一步

先为上述五条生产可达链补充调用图和 targeted contract tests，验证每条链的对象身份、market_date、PIT/freshness、degradation、writer 和通知正文边界；再依据测试结果集中迁移或加硬性生产可达性门禁。未完成前，不能关闭 #1447。
