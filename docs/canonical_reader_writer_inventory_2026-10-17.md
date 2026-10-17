# Canonical reader/writer 与 Legacy 路径清单（2026-10-17）

## 目的

这是 Issue #1447 的第一阶段清单。它只登记 current main 中已经发现的候选消费者和写入者，不把“被搜索到”误认为“生产可达”。第二阶段必须对每一项补充 owner、调用入口、写入对象、是否由 workflow 调用、失败语义和测试证据。

## 已确认的核心 owner

| canonical对象/事实 | 主生产入口 | 关联脚本/配置 | 备注 |
|---|---|---|---|
| provider有效策略 | `scripts/provider_policy.py` + `config/market/provider_priority.json` | `cloud_runner_snapshot.py`、consistency core | 应成为所有A股ETF对象路由的唯一解析入口 |
| 市场正式事实 | `market-snapshot.yml`及其market-data writer | `CURRENT.json`、query refresh | 查询补采不得形成第二正式行情writer |
| 风险证据 | `build_skfolio_risk_evidence.py` | risk evidence state、e2e projection | 必须绑定估值截止日和freshness |
| 系统状态投影 | `build_e2e_status.py` | `e2e_status.json`、Dashboard | 不能重新推断上游事实 |
| REPORT/通知 | `decision-notification.yml`、`notification_center.py` | REPORT、notification_center、PushPlus | 三类任务共享transport但不共享业务身份/正文 |

## 需要逐项审计的直接消费者/写入候选

- 对象与行情：`cloud_runner_snapshot.py`、`query_market_object.py`、`query_time_market_refresh.py`、`query_time_market_refresh_legacy.py`、`emergency_market_evidence.py`。
- 监测与观察池：`observation_etf_management.py`、`formal_etf_opportunity_discovery.py`、`build_query_context.py`、`build_stock_context.py`。
- 研究与风险：`build_research_features.py`、`build_research_execution_bridge.py`、`build_strategy_equity...`、`build_skfolio_risk_evidence.py`。
- 状态与一致性：`build_e2e_status.py`、`check_system_consistency.py`、`check_system_consistency_core.py`、`state_manager.py`、`runtime_self_heal.py`。
- 账户与旧路径：`build_account_stock_market_legacy.py`、`confirmed_trade_facts.py`、`process_state_sync_request.py`。
- 通知与运行保障：`notification_center.py`、`maintenance_guard.py`、`workflow_failure_guard.py`及相关workflow。

## 必须补齐的字段

每个候选路径必须记录：

1. 生产可达入口和调用workflow；
2. 读取/写入的canonical对象；
3. 是否直接读取配置或状态，是否绕过统一resolver；
4. 身份、PIT、market_date、cutoff、freshness和degradation如何传递；
5. 失败时是fail-closed、降级、重试、复用还是静默成功；
6. 是否可能生成第二个request、completion、event、REPORT、notification或writer；
7. 对应的targeted、adjacent、merged-main和真实E2E证据；
8. 若为legacy/compat/recovery，明确可达条件和退休条件。

## 本阶段结论

当前不能把“已新增默认provider resolver”认定为全域修复。真正的闭环要求是：所有生产可达路径都通过同一对象/事实准入和同一状态传播合同；无法收敛的路径必须被明确标记为恢复专用并禁止成为正常生产入口。