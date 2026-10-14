# 三项ETF定时任务主动通知闭环审计与集中修复报告｜2026-10-14

## 1. 审计边界与状态

审计基于 current `main`（审计起点 `eddbc15726620c51c22a0840a7b966b50e8dbfc9`），未触发任何正式任务、PushPlus 或真实生产验收。当前状态保持：

`FORMAL_DECISION_CHAIN_FAIL`

## 2. 历史成功路径回溯

### ETF系统复核

可回读的 canonical completion 示例：

- `events/reviews/system_scheduled-20261011-193000.json`
- `events/reviews/system_scheduled-20261012-083000.json`

入口使用 `STATE_SYNC_ONLY` / `FORMAL_SCHEDULED_SYSTEM_REVIEW`，由 `record_scheduled_system_review` 写入 `events/reviews/system_<occurrence>.json`。事件携带任务级 `presentation_binding`，随后由既有通知 workflow 按唯一 review event 投影通知。它不创建 Formal Decision request。

### ETF交易复盘

2026-10-13 成功路径可回读：

- `requests/live_snapshot/scheduled-trade-review-scheduled-20261013-123000.json`
- `events/reviews/2026-10-13.json`

入口使用 `STATE_SYNC_ONLY` / `FORMAL_POST_CLOSE_REVIEW` / `MIDDAY_REVIEW`，由 `record_post_close_review` 写入 canonical review event，再由同一 event 投影 REPORT_DELIVERY_REQUEST。当前实现原本在 canonical owner 中重新计算 `report_id`，这会覆盖 completion ingress 携带的任务级绑定，已在本修复中移除。

### ETF正式决策

2026-10-11 的可回读成功候选以 Formal Decision 的 `BUSINESS_DECISION_SOURCE` 入口保存于：

- `requests/live_snapshot/fd_20261011_0750_65a9eb3d__business_decision_source.json`
- `requests/live_snapshot/fd-20261011-141001-6f3a8c21__business_decision_source.json`

该路径与 Scheduled Review 不同：父请求为 `MARKET_QUOTE_REFRESH`，Actor回答通过 `BUSINESS_DECISION_SOURCE`，由 Formal Decision canonical owner 投影 Decision Fact/completion；通知只能消费该 completion 的 `presentation_binding.full_content`。不得将其与两类 Scheduled Review 的 `request_type`、`event_type` 或 completion writer 机械统一。

### 2026-09-24 至 2026-09-27

仓库现存历史 `requests/report_delivery/` 与 `notification_center` 状态可证明旧 REPORT 兼容链存在，但当前可回读材料不足以把历史 provider 结果绑定到本次 12:30 的 task_run/occurrence。历史 PushPlus 结果不作为当前发送证据。

## 3. 当前 12:30 失败链

当前唯一边界：

- `task_id`: ETF交易复盘
- `task_run_id`: `scheduled-20261014-123225`
- `occurrence_id`: `scheduled-20261014-123225`
- request: `requests/live_snapshot/scheduled-trade-review-scheduled-20261014-123225.json`
- `request_id`: `scheduled-trade-review-scheduled-20261014-123225`
- `request_type`: `STATE_SYNC_ONLY`
- `formal_fact_type`: `FORMAL_POST_CLOSE_REVIEW`
- `source`: `CHATGPT_SCHEDULED_REVIEW_ACTOR`
- `presentation_binding.report_id`: `ETF_TRADE_REVIEW:scheduled-20261014-123225`
- `presentation_binding.content_hash`: `fe59f0f82b3d7e72e51f0fec50bf2b31b48cdb135cdea12f52b58091f293f701`

当前链条结果：

`request` → `presentation_binding/full_content` 已存在 → canonical owner 校验失败 → review completion 未形成 → REPORT 未形成 → REPORT_DELIVERY_REQUEST 未形成 → notification_id 不存在 → PushPlus 未调用/当前 provider result 不存在 → 用户收到状态未确认。

GitHub Actions run `38512066799` 的 `snapshot` job 在 `process_state_sync_request.py:2292` 明确失败：

`ValueError: scheduled completion presentation_binding content_hash mismatch`

因此本次断点是“completion ingress 已提交，但 canonical completion writer 因正文指纹不一致 fail-closed”，不是 PushPlus provider 失败。

## 4. 修复判断

已确认的实现偏离：

1. 交易复盘 canonical owner 在 `process_state_sync_request.py` 中重新生成 `etf_trade_review:<task_run_id>`，覆盖 completion binding 携带的 `report_id`。
2. 该逻辑与“当前 completion binding 是 report_id 唯一来源”不一致。
3. 当前 12:30 的 hash mismatch 仍应 fail-closed，不能由旧 REPORT、旧 notification 或历史 provider 结果补齐正文；本修复不放宽该门禁。
4. 当前任务指令不在本 PR 中重写；指令差异需基于成功案例进一步在任务管理侧逐项修正。

## 5. 代码与测试修改

修改文件：

- `scripts/process_state_sync_request.py`：保留当前 completion binding 的 `report_id`，并以该值生成 REPORT 投影路径和内容绑定。
- `tests/test_post_close_review_canonical.py`：增加自定义 canonical `report_id`、`occurrence_id`、`content_hash` 回归断言，防止 owner 机械重算。

未新增 producer、checker、workflow、state、sender、REPORT 类型或通知链；未修改 MASTER、provider priority、monitor universe、账户事实、行情链、交易权限或自动下单能力。

## 6. 尚未验证项

- 当前 12:30 的原始 `full_content` 与 `content_hash` 为什么在 Actor 生成阶段不一致，仍需任务运行侧追溯最终字符串和哈希计算位置。
- 三类任务当前 completion、REPORT、notification_id 与 PushPlus provider result 尚未完成三入口真实验收。
- 当前 12:30 没有 request-bound notification_id；当前 PushPlus provider result 不存在。
- 用户已看到的 ChatGPT正文不能替代 completion 或通知证据。

## 7. 结论

本轮完成的是只读成功案例回溯、当前失败断点定位及最小代码回归修复；未进行真实验收，未发送 PushPlus。状态必须继续保持：

`FORMAL_DECISION_CHAIN_FAIL`
