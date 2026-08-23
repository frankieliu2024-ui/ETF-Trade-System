# ETF-Trade-System 统一读取入口

本文件是仓库结构入口，不是交易规则。GitHub `main` 是云端唯一主版本；ChatGPT、Codex 和 GitHub Actions 均从本索引解析正式路径。

## 四个正式文件

| 职责 | 唯一路径 | 说明 |
|---|---|---|
| 规则 | `ETF规则_MASTER.md` | 交易规则唯一来源；不得自动修改 |
| 当前状态 | `ETF当前状态_DASHBOARD.md` | 当前状态和账户事实入口；规则不在此定义 |
| 经验复盘 | `ETF交易复盘与经验库_2026.md` | CASE、OBS、复盘历史 |
| 行情事实 | `ETF市场行情档案_2026.md` | 行情、成交、来源和质量事实 |

历史聊天不属于正式来源。发生冲突时遵循 MASTER > Dashboard > 当前行情与成交 > 经验库 > 行情档案 > 历史聊天。

## 运行读取路径

- 状态：`data/state/CURRENT.json`、`data/state/account_fact.json`、`data/state/review_context.json`、`data/state/chatgpt_task_probe.json`
- 运行健康：`data/state/runtime_health.json`
- 实时相邻变化：`data/state/market_delta.json`
- 正式海外与亚洲指数层：`data/state/overseas_context.json`（NDX、SOX、N225、KOSPI、TWII、HSTECH）
- 监测配置：`config/market/market_monitor_config.json`
- 运行策略：`config/runtime_policy.json`
- 行情：`data/market/snapshots/`
- 审计：`data/market/audit/`
- 查询上下文：`data/state/query_context.json`、`data/state/decision_context.json`
- 云端 workflow：`.github/workflows/market-snapshot.yml`
- 运行脚本：`scripts/`
- 测试：`tests/`；回放：`tests/replay/`；验收：`tests/validation/`
- 历史报告：`archive/reports/`

## 实时读取原则

云端采集目标频率不等于决策时钟。GitHub Actions可能存在调度排队，行情接口也可能延迟，因此任何“当前ETF判断”必须以 `CURRENT.json` 的 `captured_at` 为基准，结合 `config/runtime_policy.json` 重新计算数据年龄，并读取 `runtime_health.json`。

- `FRESH`：可作为当前行情事实进入正式决策链。
- `DEGRADED`：仅作背景和连续性复核；若会改变机会、金额或卖出动作，优先等待下一有效脉冲或结合用户当前截图复核。
- `STALE`：不得冒充实时行情，只能作历史/背景事实。
- 采集失败：上一有效 `CURRENT` 与快照继续保留；失败状态记录在 `runtime_health.json`，下一采集脉冲自动恢复，不用失败数据覆盖有效状态。
- 收盘补采：15:00计划任务允许在 `runtime_policy.close_grace_seconds` 宽限窗口内补采；一旦当日 `close` 已成功写入，后续重复收盘任务跳过。

## 账户事实原则

`account_fact.json` 的 `VALID` 仅表示该份截图/账户事实本身通过校验，不表示可以跨交易日自动沿用。正式盘中或盘后决策必须同时满足：

1. `account_fact.status == VALID`；
2. `account_fact.updated_at` 对应上海日期与当前 `CURRENT.market_date` 一致。

不满足时，ChatGPT必须要求当日券商截图或当日账户确认，不得用旧Dashboard推定持仓、现金或成交未变化。

## 海外与亚洲指数原则

生产链独立生成 `overseas_context.json`，正式对象固定为：纳斯达克100指数（NDX）、费城半导体指数（SOX）、日经225指数（N225）、韩国综合指数（KOSPI）、台湾加权指数（TWII）、恒生科技指数（HSTECH）。

- 六个对象都属于正式指数层；单一对象数据失败可以DEGRADED／FAILED，但不得静默从正式查询中遗漏；
- 直接指数优先。SOXQ、513520、513180等仅在对应直接指数不可用时承担明确的备用代理职责，不得写成指数本身；
- HSTECH优先尝试同花顺HS2083，失败后再使用允许的替代数据源或513180代理；
- 单个海外对象失败不阻断A股八ETF、上证指数、创业板指的核心行情脉冲；
- 海外与亚洲指数不是次要信息，但只作市场背景、增强证据或反向证据，仍须经过本地传导和目标ETF自身反馈才能进入交易判断。

### 跨市场时间对齐

任何跨市场比较必须同时读取并展示：`market_timezone`、`latest.as_of_local`、`market_phase_at_generation`、`time_relation_to_a_share`。

- 美国现金指数：A股交易时段通常对应上一美股交易时段，不得称为与A股当前盘中“同步”；
- 日经、韩国、台湾、恒生科技：根据各自市场阶段区分同日盘中、同日已收盘、上一交易日；
- A股15:00收盘不代表恒生科技已经当日收盘；
- 日期相同不等于时点同步，不允许把上一收盘、当前盘中和当日收盘混成同一组共振证据。

## 故障退化表

|故障|系统动作|决策边界|
|---|---|---|
|GitHub Actions调度延迟|按 `captured_at` 重新计算数据年龄|不按cron计划时刻冒充行情时点|
|单次hithink采集失败|保留上一有效CURRENT；记录FAILED|旧行情按FRESH/DEGRADED/STALE处理|
|旧任务晚到|SUPERSEDED或被并发策略取消|不得覆盖更新快照|
|15:00任务延迟|宽限窗口补采close|已存在成功close则不重复覆盖|
|海外/亚洲单个指数失败|该对象FAILED，其余对象继续|失败对象不参与当前证据，但监测职责不删除|
|跨市场时点不一致|显式标注各自as_of和market_phase|不得称为同步共振|
|账户事实来自上一交易日|账户门禁判不可用|必须补当日账户事实|
|Git推送遇到同期人工/Codex提交|workflow先rebase再推送|不得覆盖正式文件；冲突时以保留数据和人工检查优先|
|行情STALE|明确标注数据不足|不得输出依赖实时价格成立的新增金额或卖出动作|

## 读取场景

1. ChatGPT规则读取：先读本索引，再读一级目录的 `ETF规则_MASTER.md`。
2. ChatGPT当前状态：先读 `CURRENT.json`、`runtime_health.json` 和运行策略；需要人工当前状态时再读一级目录的 `ETF当前状态_DASHBOARD.md`。
3. 盘中查询：读 `CURRENT.json`、最新 `data/market/snapshots/`、`market_delta.json`、`overseas_context.json`、`runtime_health.json` 和 `data/state/query_context.json`；按查询时刻重新判断新鲜度，不绑定固定截图节点。
4. 盘后维护：读 `post_market_review/post_market_review_event.json`、`data/state/review_context.json`、账户事实和四个正式文件。

## 写入边界

自动程序可以生成状态、行情、审计、候选草稿、运行健康状态和报告；不得自动写 MASTER，不得生成交易动作，不得把历史回放文件当生产状态。
