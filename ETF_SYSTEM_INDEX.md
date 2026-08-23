# ETF-Trade-System 统一读取入口

本文件是仓库结构入口，不是交易规则。GitHub `main` 是云端唯一主版本；ChatGPT、Codex 和 GitHub Actions 均从本索引解析正式路径。

## 四个正式文件

| 职责 | 唯一路径 | 说明 |
|---|---|---|
| 规则 | `docs/rules/ETF规则_MASTER.md` | 交易规则唯一来源；不得自动修改 |
| 当前状态 | `docs/state/ETF当前状态_DASHBOARD.md` | 当前状态和账户事实入口；规则不在此定义 |
| 经验复盘 | `docs/review/ETF交易复盘与经验库_2026.md` | CASE、OBS、复盘历史 |
| 行情事实 | `docs/market/ETF市场行情档案_2026.md` | 行情、成交、来源和质量事实 |

历史聊天不属于正式来源。发生冲突时遵循 MASTER > Dashboard > 当前行情与成交 > 经验库 > 行情档案 > 历史聊天。

## 运行读取路径

- 状态：`data/state/CURRENT.json`、`data/state/account_fact.json`、`data/state/review_context.json`
- 行情：`data/market/snapshots/`
- 审计：`data/market/audit/`
- 查询上下文：`query_context.json`、`decision_context.json`
- 云端 workflow：`.github/workflows/market-snapshot.yml`
- 运行脚本：`scripts/`
- 测试：`tests/`；回放：`tests/replay/`；验收：`tests/validation/`
- 历史报告：`archive/reports/`

## 读取场景

1. ChatGPT规则读取：先读本索引，再读 `docs/rules/ETF规则_MASTER.md`。
2. ChatGPT当前状态：读 `data/state/CURRENT.json`，需要人工当前状态时再读 `docs/state/ETF当前状态_DASHBOARD.md`。
3. 盘中查询：读 `CURRENT.json`、最新 `data/market/snapshots/` 和 `query_context.json`。
4. 盘后维护：读 `post_market_review/post_market_review_event.json`、`review_context.json`、账户事实和四个正式文件。

## 写入边界

自动程序可以生成状态、行情、审计、候选草稿和报告；不得自动写 MASTER，不得生成交易动作，不得把历史回放文件当生产状态。
