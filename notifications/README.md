# 通知事件层

通知层只记录系统状态事件，不生成盘中主动交易提醒。允许的事件类型为：

- `POST_MARKET_REVIEW_REQUIRED`
- `SYSTEM_ERROR`
- `DATA_DEGRADED`
- `ACCOUNT_FACT_REQUIRED`

盘中节点继续由云 Runner 维护行情、CURRENT 和上下文；默认不生成用户催促通知。历史通知模拟文件保留在 `tests/replay/notifications/`，仅用于回放审计。

所有事件禁止包含交易建议、买卖动作、金额或订单。
