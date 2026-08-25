# 通知事件层

通知层只推送需要用户关注、确认或重新评估的事项，不生成盘中交易动作。现有 PushPlus 链保持不变，通知记录由 `data/state/notification_center.json` 管理。

## 生命周期

通知记录使用 `CREATED → SENT/WAITING_CONFIRMATION → CONFIRMED → ARCHIVED`，超过确认窗口进入 `EXPIRED`。同一 `source_event_id` 在未关闭或已关闭时不重复推送；失败/未发送不会被当作用户已确认。

需要确认的延迟执行事件统一标记为 `PENDING_EXECUTION_CONFIRMATION`。用户只能通过明确的“确认 / 否 / 补充成交明细”入口改变其状态；确认入口随后复用 `scripts/process_state_sync_request.py` 写入正式账户事实和 `events/trades`，不从最终持仓猜测成交。

确认成功后生成现有研究目录下的 `TRADE_COMPLETED_REVIEW_REQUIRED` 客观复盘待办，保留 `execution_date` 与 `confirmation_date` 两个时点。复盘待办不自动判断交易正确/错误，不修改 MASTER，不生成新的交易动作。

系统事件、收盘账户提醒和决策重评仍由现有 `.github/workflows/decision-notification.yml` 驱动；没有新增 workflow、provider 或行情请求。确认请求通过 `scripts/confirm_execution_reconciliation.py <request.json>` 进入；没有明确确认或必要成交字段时，只保留待确认问题，不写入正式成交事实。

## 微信通知的人机界面要求

每一条真实通知必须让用户在几秒内回答以下问题：

1. 发生了什么；
2. 是哪个模块、证券或账户事实；
3. 是否影响行情、账户或交易判断；
4. 用户现在是否需要操作、需要做什么；
5. 系统下一步如何处理。

测试通知标题必须明确包含“【测试】”，正文必须说明“不代表真实行情、账户、交易或系统故障”。真实系统异常标题必须明确包含“【真实运行异常】”及用户可理解的影响等级，例如“需要关注”或“影响交易判断”。

通知状态可以保存 `user_severity` 和 `user_action` 作为用户界面字段，但不得把内部 `E2E`、`BLOCKED`、`ESCALATE`、异常分类代码等直接当成主要微信文案。

历史 workflow 失败如果已被更新的 `main` 提交取代，不得作为当前真实故障继续推送。只有诊断对应当前 `main` 时，才允许发送系统故障通知；通知必须列明 workflow、Run ID、具体失败步骤、影响和用户动作。
