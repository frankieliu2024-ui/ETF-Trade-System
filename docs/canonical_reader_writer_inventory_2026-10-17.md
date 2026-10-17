# Canonical reader/writer 与 Legacy 路径清单（2026-10-17）

## 目的

这是 Issue #1447 的第二阶段审计产物。它区分“代码存在”“被测试引用”和“由生产入口可达”，不把被搜索到自动视为生产链。正式规则仍按 `ETF_SYSTEM_INDEX.md` 路由读取。

## 已确认的核心 owner

| canonical对象/事实 | 主生产入口 | 关联脚本/配置 | 当前结论 |
|---|---|---|---|
| provider有效策略 | `scripts/provider_policy.py` + `config/market/provider_priority.json` | `cloud_runner_snapshot.py`、`check_system_consistency_core.py` | 已有统一resolver，但尚未证明所有生产消费者均已接入 |
| 市场正式事实 | `.github/workflows/market-snapshot.yml`及其market-data writer | `CURRENT.json`、`query_time_market_refresh.py` | workflow明确触发；查询补采必须继续复核是否只回到同一writer |
| 风险证据 | `build_state_context.py`调用 `build_skfolio_risk_evidence.py` | risk evidence state、e2e projection | 已确认属于状态重建链；需核对旧估值截止日如何阻断下游 |
| 系统状态投影 | `build_e2e_status.py`、`system-consistency.yml` | `e2e_status.json`、Dashboard | 生产可达；不得重新推断上游事实或掩盖降级 |
| REPORT/通知 | `decision-notification.yml`、`notification_center.py` | REPORT、notification_center、PushPlus | 生产可达；三类任务共享transport但不共享业务身份/正文 |

## 生产可达性审计结果

| 路径 | 生产入口证据 | 读取/写入边界 | 当前风险 | 处置 |
|---|---|---|---|---|
| A股行情主链 | `market-snapshot.yml`的paths含 `requests/live_snapshot/**`、`scripts/cloud_runner_snapshot.py`、`scripts/query_time_market_refresh.py`、provider配置 | 读取对象全集和provider配置，写入正式行情及相关投影 | 查询、应急和legacy路径可能各自解释对象或质量 | 纳入统一对象解析与质量传播回归 |
| provider解析 | `cloud_runner_snapshot.py`已导入 `resolve_object_policy`；一致性检查已导入 `effective_etf_policies` | 统一默认A股ETF链，显式对象策略优先 | 其他直接读取 `object_fallback_policy` 的消费者仍需逐一确认 | 不再补对象；清理/接入直接消费者 |
| 查询时补采 | `query_market_object.py`、`query_time_market_refresh.py`被测试和market workflow引用 | 生成或消费单对象查询事实 | 可能绕过正式provider resolver或产生第二解释层 | 与主行情writer建立同一 resolver/质量合同 |
| 风险重建 | `build_state_context.py`直接调用 `build_skfolio_risk_evidence.py`；market workflow含显式重建步骤 | 写入风险证据、strategy equity和研究派生状态 | 估值截止日落后时，需确保状态为待补/降级而非最新可用 | 增加旧截止日、补齐、恢复三态测试 |
| 应急证据 | `process_state_sync_request.py`导入 `emergency_market_evidence.py` | 作为请求处理中的外部证据校验 | 必须证明只作为合法补充，不成为第二行情writer | 标记为request-bound/recovery consumer，禁止旁路持久化 |
| legacy查询 | `query_time_market_refresh_legacy.py`存在并被测试引用 | 兼容查询路径 | 生产可达性尚未证实；若可达则可能形成第二路由 | 下一步核实workflow调用；无生产调用则标记recovery-only |
| legacy账户市场 | `build_account_stock_market_legacy.py`存在并直接读取对象策略 | 账户/市场兼容投影 | 可能绕过统一resolver；不能仅因legacy命名而忽略 | 核实调用入口；若无生产调用，加入禁止正常入口测试 |
| 观察与Discovery | `observation_etf_management.py`、`formal_etf_opportunity_discovery.py` | 维护观察/机会候选及正式上下文 | 新对象身份、provider、PIT/freshness可能未在同一准入点完成 | 建立新增对象全链路测试和统一准入函数 |

## 已确认的工作流/调用关系

1. `market-snapshot.yml`明确包含请求、行情脚本、provider配置和查询时补采路径；这是行情主链的生产入口。
2. `build_state_context.py`调用风险证据builder；风险证据不是孤立文件，而是状态上下文重建的一部分。
3. `process_state_sync_request.py`导入应急行情证据校验；该路径与Formal Decision请求处理相关，不能直接演变为第二事实生产链。
4. `system-consistency.yml`将查询刷新测试和脚本变更纳入一致性检查，但测试引用不等于生产可达。
5. `cloud_runner_snapshot.py`和一致性核心已接入provider resolver；这只覆盖已确认的两个消费者，不能代表所有读取者已收敛。

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

## 第二阶段结论

已确认至少存在一条生产可达行情主链、一条风险证据重建链和一条正式通知链；同时存在需要排除或收敛的查询、应急、legacy和Discovery候选路径。当前仍不能把“默认provider resolver已合并”认定为全域修复。下一步应优先实现生产可达路径的统一对象/事实准入和降级传播测试，再对确认绕过resolver的消费者实施代码收敛。
