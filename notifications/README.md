# 通知事件层

通知层只推送需要用户关注、确认或重新评估的事项，不生成盘中交易动作。现有 PushPlus 链保持不变，通知记录由 `data/state/notification_center.json` 管理。

## 生命周期

通知记录使用 `CREATED → SENT/WAITING_CONFIRMATION → CONFIRMED → ARCHIVED`，超过确认窗口进入 `EXPIRED`。同一 `source_event_id` 在未关闭或已关闭时不重复推送；失败/未发送不会被当作用户已确认。

需要确认的延迟执行事件统一标记为 `PENDING_EXECUTION_CONFIRMATION`。用户只能通过明确的“确认 / 否 / 补充成交明细”入口改变其状态；确认入口随后复用 `scripts/process_state_sync_request.py` 写入正式账户事实和 `events/trades`，不从最终持仓猜测成交。

确认成功后生成现有研究目录下的 `TRADE_COMPLETED_REVIEW_REQUIRED` 客观复盘待办，保留 `execution_date` 与 `confirmation_date` 两个时点。复盘待办不自动判断交易正确/错误，不修改 MASTER，不生成新的交易动作。

系统事件、收盘账户提醒和决策重评仍由现有 `.github/workflows/decision-notification.yml` 驱动；本次没有新增 workflow、provider 或行情请求。
