# ETF-Trade-System 仓库结构治理前清单

盘点日期：2026-08-23。工作树在盘点前为 clean；Git 分支 `main` 与 `origin/main` 同步。

| 路径/类别 | 用途 | 生产依赖 |
|---|---|---|
| `system/ETF规则_MASTER.md` | 交易规则唯一正式来源 | 是，人工读取；禁止程序改写 |
| `system/ETF当前状态_DASHBOARD.md` | 当前账户、生命周期、当前状态入口 | 是，`scripts/state_manager.py` 读取 |
| `system/ETF交易复盘与经验库_2026.md` | CASE/OBS/复盘历史 | 否，人工复盘来源 |
| `system/ETF市场行情档案_2026.md` | 行情、成交和数据质量事实档案 | 否，人工事实来源 |
| `system/ETF行情数据接口使用规范_V1.0.md` | 数据接口和质量边界 | 否，规范入口 |
| `.github/workflows/market-snapshot.yml` | 云端定时采集和状态构建 | 是 |
| `scripts/` | 采集、状态、上下文、质量检查 | 是 |
| `data/state/` | CURRENT、账户事实和状态候选 | 是 |
| `data/market/snapshots/` | 生产及回放行情快照 | 是/历史回放混合 |
| `data/market/audit/` | 数据质量审计摘要 | 否，审计留证 |
| `tests/` | 状态层、交互和数据能力测试 | 否，CI/人工验证 |
| `backtest_replay/`、`replay_sources/`、`notifications/replay/` | 历史回放和通知模拟 | 否，历史验证 |
| `research/backtests/` | 专项回测和验收结果 | 否，历史证据 |
| `history/` | 冻结的历史成交 Excel 基线 | 否，人工/历史事实 |
| 根目录 `*报告*.md`、阶段报告 | 历史交付报告 | 否，历史归档 |
| `README.md`、`chatgpt_task_probe.json`、`query_context.json` 等 | 项目说明和读取上下文 | README 是说明；上下文由 workflow 更新 |

## 已确认的路径引用

- workflow 直接运行 `scripts/`，写入 `data/state/`、`data/market/snapshots/`、`decision_context.json`、`query_context.json`、`review_context.json` 和 `events/events.jsonl`。
- `scripts/state_manager.py` 直接读取 `system/ETF当前状态_DASHBOARD.md`。
- `README.md` 直接引用 `system/ETF规则_MASTER.md`。
- 测试夹具在临时 root 下创建 `system/` 和 Dashboard 文件，不应把临时夹具路径误认为生产路径。

## 治理前正式文件 SHA256

```text
8494FBCE478C401613149417AFFD9CF7D57B96D019CBF93A3252766DDC9E23CA  system/ETF规则_MASTER.md
C0DE7113C3529E6882CD36324004D8A3C9A578728032521A64922AECA96E16CD  system/ETF当前状态_DASHBOARD.md
1C2D29D15E4B7F5C48EEA25550309D58F910D99F13F636F6B1FF348F80F7690B  system/ETF交易复盘与经验库_2026.md
6A89C5AB69ECD06A08F5AB56DAD0D441ACF503CAC4E43DEB12E46A647C099F62  system/ETF市场行情档案_2026.md
```
