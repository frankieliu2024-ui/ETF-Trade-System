# 状态说明：历史重建方案，现已退役

> 本文件仅保留为V2.1历史审计和回溯材料，不是当前【ETF正式决策】或其他两个ETF定时任务的现行规则源。当前三个任务的完整指令、节点、身份和回退基线以 `docs/automation_prompt_history/ETF三个定时任务冻结封存_2026-10-16.md` 及 current main 所登记的正式协议、workflow和canonical owner为准。
>
> 本文件中的“两个定时任务”、10:00起始节点、`STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW`旧路径和定时任务专属作用域，不得覆盖当前已启用的三任务调度与现行Formal Decision主链。保留本文件不表示恢复、启用或继续维护该旧方案。

---

# Formal Decision 定时任务重建合同 V2

版本：V2.1  
适用范围：仅限【ETF正式决策】定时任务。  
唯一规则来源：本文件由 `ETF_SYSTEM_INDEX.md` 路由，交易权限、风险阈值、观察/机会/持仓定义仍分别服从 MASTER、数据规范和 Formal Decision 执行协议。

本文件只补充两个定时任务入口的运行身份、自动节点和手动启动规则；它不再定义第二条 Formal Decision 生产链。对话窗口、手动任务和自动任务三者的业务判断、唯一正文、STATE_SYNC_ONLY、review event、REPORT、notification_center 和 PushPlus 统一服从 `docs/Formal Decision执行与用户回复协议.md`。

## 0. 作用域防火墙（强制）

本文件是 `MANUAL_SCHEDULED_TASK` 与 `AUTOMATIC_SCHEDULED_TASK` 的唯一执行合同。它们不得读取、等待、复用或消费 `MANUAL_CONVERSATION` 的 `formal_reply_gate`、query_context、DWP、父请求、BDS、`MARKET_QUOTE_REFRESH` 或 `FORMAL_DECISION_COMPLETION`。这些旧对象即使存在，也不能成为本次定时任务的前置门禁或事实来源。

定时任务只建立本次 task/run/occurrence/request 身份，读取当前正式事实，完成本合同要求的业务覆盖，并以 `STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW` 进入既有 Scheduled Review completion/review-event/通知主链。两个定时入口与对话入口共享同一业务、completion、review event、REPORT和通知合同；每次运行的 request/run/occurrence 身份值必须各自唯一，不能互用。

## 一、与两个稳定任务的边界

【ETF系统复核】和【ETF交易复盘】仅作为只读对照对象。不得修改其任务、调度、代码、协议、状态、completion、REPORT、notification 或 PushPlus 链。本任务逐节点复用二者已经稳定的 Scheduled Review 请求、completion、canonical review event、REPORT、notification_center 和统一 PushPlus 链；Formal 的业务字段与正文不同，但生产链不分叉。

## 二、两个定时任务入口（不产生第二生产链）

用户明确手动启动本任务时，入口必须是：

- `source=CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS`
- `entry_mode=MANUAL_SCHEDULED_TASK`

手动入口任何北京时间均可运行。

系统无人手动、按自动化日历唤起时，入口必须是：

- `source=CHATGPT_SCHEDULED_FORMAL_ANALYSIS`
- `entry_mode=AUTOMATIC_SCHEDULED_TASK`

自动入口只允许北京时间 10:00、11:00、13:30、14:30，且必须是A股交易日和适用节点；否则只 SKIP，不创建请求、不消费历史请求、不生成正文。不得仅因任务名称含“定时任务”把手动启动误判为自动入口。

## 三、运行身份

每次有效运行必须使用当前运行时提供的：

`task_id`、`task_run_id`、`occurrence_id`、`parent_request_id`、`request_id`、`market_date`。

自动入口的 `task_id`、`task_run_id` 和 `occurrence_id` 不得由脚本默认值或模型推测生成；缺失即 fail-closed。自动 `occurrence_id` 必须以 `scheduled_` 开头。一次运行只能绑定一个当前父请求，重复继续只能消费同一父请求，不能创建平行请求。

## 四、请求与事实链

入口先建立并回读 `requests/live_snapshot/<request_id>.json`，请求类型固定为 `STATE_SYNC_ONLY`，事实类型固定为 `FORMAL_SCHEDULED_REVIEW`，来源固定为 `CHATGPT_SCHEDULED_REVIEW_ACTOR`，并携带 `task_id=ETF正式决策`、本次唯一 `task_run_id/occurrence_id`、`market_date` 和完整 `presentation_binding`。Formal 业务判断仍按本合同第五节完成，但请求分类、身份绑定、完成入口和终态处理逐字段对齐两个稳定 Scheduled Review。不得在本定时任务入口创建 `MARKET_QUOTE_REFRESH`、BUSINESS_DECISION_SOURCE、BDS 或独立 Formal completion。身份、正文、哈希、时点或 binding 未追上时，只能沿同一运行身份继续或 fail-closed；不得创建第二请求、消费历史请求或按目录猜测。

## 五、READY后的业务与正文

本任务不等待 MANUAL_CONVERSATION 的 READY、formal_reply_gate 或历史 DWP；必须完成本次当前事实覆盖和以下业务问题（若本次运行已生成并明确绑定 Scheduled Review 问题包，则按该问题包逐项覆盖）：风险许可；海外与跨市场；A股内部反馈；ETF与资本机会；全市场机会发现；全部观察ETF；当前机会ETF；全部实际持仓；每只持仓是否追加；现金、可释放资本、资金缺口；Trial/Confirm资格；实际部署金额；最终动作；关键风险；改变动作的条件及DWP登记的其他问题。

只冻结一份 `FINAL_CONTENT`，严格使用 Formal Decision 固定标题、章节顺序、编号、证券“名称（六位代码）”格式和用户可见边界。观察ETF只有同一DWP六项 thesis 字段完整时才可 RETAIN；缺失时不得补造，只能按当前事实 EXIT，或在DWP允许时提交完整当前 ADMIT thesis。不得自动下单。

## 六、后置投影

用户窗口或任务运行内部只冻结一份完整且不可变的 `FINAL_CONTENT`。随后同一运行只提交一次现有 Scheduled Review completion ingress：

1. 请求必须保持 `request_type=STATE_SYNC_ONLY`、`formal_fact_type=FORMAL_SCHEDULED_REVIEW`、`source=CHATGPT_SCHEDULED_REVIEW_ACTOR`；
2. `presentation_binding` 必须包含 `report_type=ETF_FORMAL_DECISION`、`task_id`、`task_run_id`、`report_id`、`full_content`、`content_hash` 和 `market_date`；
3. canonical state-sync owner 只形成唯一 `events/reviews/formal_<occurrence_id>.json`，同一 occurrence 只能首次写入一次，重放只能幂等返回；
4. `market-snapshot.yml` 只负责处理请求并持久化该 review event；`decision-notification.yml` 通过 `workflow_run` 的 downstream producer commit 精确绑定 `events/reviews/formal_<occurrence_id>.json`，再由唯一 `notification_center` 投影 `ETF_FORMAL_DECISION` 的 `FULL_REPORT`，沿统一 guarded sender、PushPlus、notification state 和 provider 证据链投递；producer 不得直接执行通知；
5. 禁止在 completion 后重新分析、改写正文或创建第二正文、第二 event、`requests/report_delivery`、`requests/report_handoff`、独立 workflow、独立 sender 或直接 PushPlus。完整报告仍是信息交付，`no_trade_authority=true`，用户自行决定并手动下单。

## 七、验收

代码验收必须逐项覆盖：Formal 请求字段与两个稳定 Scheduled Review 的字段相同；Formal completion 为 `STATE_SYNC_ONLY`；`FORMAL_SCHEDULED_REVIEW` 只能形成 `events/reviews/formal_<occurrence_id>.json`；正文哈希、task/run identity、REPORT projection、幂等重放和通知唤醒均 fail-closed。稳定任务的实现、状态和测试必须保持不变。

真实目标路径验收必须核对：

`任务运行 → STATE_SYNC_ONLY request → FORMAL_SCHEDULED_REVIEW → FINAL_CONTENT → canonical review event → ETF_FORMAL_DECISION REPORT → notification_center → PushPlus`

不得把旧的 `MARKET_QUOTE_REFRESH → BDS → Formal completion → workflow_dispatch` 当作本定时任务验收路径。CI通过不等于真实通知成功；任务建立、运行已发起、workflow success或provider success均不等于用户实际收到。

## 八、手动启动定时任务的运行身份建立（V2.1）

“用户手动启动本自动化”与“系统按日历自动唤起”是两个不同入口。平台没有向模型暴露自动化运行凭据时，只能阻断自动入口；不得把该限制套用到用户手动启动入口。

对 `MANUAL_SCHEDULED_TASK`：

1. 用户在本自动化任务的运行界面明确发起本次运行，即是本次手动触发事实；不得因当前时间不在自动节点而 SKIP。
2. `task_id` 使用当前任务的固定正式名称“ETF正式决策”；
3. `task_run_id` 与 `occurrence_id` 必须在本次手动运行首次创建请求时一次性生成，格式为 `manual-scheduled-YYYYMMDDTHHMMSS+0800-<短随机后缀>`，并写入同一请求。该身份是本次手动 ingress 新建的当前运行身份，不得使用历史值，不得伪造为自动运行身份，也不得在后续步骤重新生成；
4. `parent_request_id`、`request_id` 和 `market_date` 从同一请求 ingress 绑定并持续复用；
5. 生成身份后必须立即写入并回读 `requests/live_snapshot/<request_id>.json`，后续本次 Scheduled Review 的事实、FINAL_CONTENT、completion、event 和通知只能绑定这一组身份；不得创建或消费 BDS，不得把历史 DWP、formal_reply_gate 或对话父请求绑定进来；
6. 若同一手动运行已经取得当前 `task_run_id/occurrence_id`，只能继续消费该父请求；不得因后续读取不到自动化平台元数据而重新判定为未知、创建第二请求或暂停任务。该请求仍必须使用 `STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW`，不得切换成旧的 `MARKET_QUOTE_REFRESH`。

对 `AUTOMATIC_SCHEDULED_TASK`，第三节的运行时注入和缺失即 fail-closed 规则保持不变；不得用本节的手动身份生成规则补齐自动入口。此次 V2.1 只修复手动启动入口的身份建立，不改变两个稳定任务、正式自动节点、交易规则或通知链。
