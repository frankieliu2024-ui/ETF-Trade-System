# Four-file maintenance boundary

- `ETF规则_MASTER.md`: read-only in automation; no program may write it.
- `ETF当前状态_DASHBOARD.md`: read-only in automation. State changes go to `data/state/dashboard_update_candidate.json` for human confirmation.
- `ETF交易复盘与经验库_2026.md`: automation may create CASE/OBS drafts only; it must not overwrite the formal file.
- `ETF市场行情档案_2026.md`: automation may record data source, quality, coverage, and anomalies only; it must not write trading judgments.
- `system/ETF行情数据接口使用规范_V1.0.md`: read-only contract reference.

The state layer does not create risk permissions, lifecycle decisions, trade amounts, buy actions, sell actions, or orders.
