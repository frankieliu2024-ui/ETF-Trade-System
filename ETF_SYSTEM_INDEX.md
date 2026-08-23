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

## 读取场景

1. ChatGPT规则读取：先读本索引，再读一级目录的 `ETF规则_MASTER.md`。
2. ChatGPT当前状态：先读 `CURRENT.json`、`runtime_health.json` 和运行策略；需要人工当前状态时再读一级目录的 `ETF当前状态_DASHBOARD.md`。
3. 盘中查询：读 `CURRENT.json`、最新 `data/market/snapshots/`、`market_delta.json`、`runtime_health.json` 和 `data/state/query_context.json`；按查询时刻重新判断新鲜度，不绑定固定截图节点。
4. 盘后维护：读 `post_market_review/post_market_review_event.json`、`data/state/review_context.json`、账户事实和四个正式文件。

## 写入边界

自动程序可以生成状态、行情、审计、候选草稿、运行健康状态和报告；不得自动写 MASTER，不得生成交易动作，不得把历史回放文件当生产状态。
