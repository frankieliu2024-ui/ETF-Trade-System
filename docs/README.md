# 正式文件入口

GitHub `main` 是 ETF-Trade-System 的云端唯一主版本。本目录承载四个正式文件，历史聊天不作为正式来源。

- `ETF规则_MASTER.md`：交易规则唯一来源，包含风险许可、Trial、Confirm、金额和卖出规则；自动程序只读。
- `ETF当前状态_DASHBOARD.md`：当前状态、账户、生命周期和下一节点；不得把规则写入此处。
- `ETF交易复盘与经验库_2026.md`：CASE、OBS 和复盘历史；正式经验需人工边界控制。
- `ETF市场行情档案_2026.md`：行情、成交、数据源、时点和质量事实；不写交易判断。

统一路径入口见根目录 `ETF_SYSTEM_INDEX.md`。运行状态和上下文见 `data/state/`，行情快照见 `data/market/snapshots/`，历史报告见 `archive/reports/`。
