# Formal Decision执行与用户回复协议

版本：统一三入口单链 V3  
适用范围：以下三个入口的每一次【ETF正式决策】分析：

1. 对话窗口：用户明确要求“@github 执行正式决策分析”；
2. 自动节点：系统按【ETF正式决策】日历自动启动；
3. 手动任务：用户在【ETF正式决策】任务界面主动启动。

本协议是三个入口共同的 Formal Decision 生产合同。三种入口只记录不同的触发身份，不得产生不同的业务判断、正文、completion、event、REPORT 或通知链。

## 一、唯一目标

每次运行都必须基于本次当前有效事实，完成一份全面、完整、可追溯的正式ETF投资判断，并将同一份冻结正文通过既有 PushPlus/微信通知链原文转发。

正式判断至少覆盖：

- 当前风险许可和整体仓位态度；
- 海外与跨市场环境；
- A股内部反馈；
- ETF机会与资本效率；
- 全市场机会发现；
- 全部观察ETF；
- 当前机会ETF；
- 全部实际持仓；
- 每只持仓是否继续持有、追加、减仓或退出；
- 现金、可释放资本、资金缺口；
- Trial/Confirm资格；
- 实际部署金额；
- 关键风险；
- 改变动作的条件；
- 信息不足、最脆弱假设、置信度和最可能出错的地方。

不得自动下单、自动调仓或改变交易权限。报告是正式投资判断，交易由用户人工确认。

## 二、三入口身份

| 入口 | trigger_source | entry_mode | 时间规则 |
|-|-|-|-|
| 对话窗口 | `CHATGPT_MANUAL_FORMAL_ANALYSIS` | `MANUAL_CONVERSATION` | 用户明确发起即可运行 |
| 手动启动任务 | `CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS` | `MANUAL_SCHEDULED_TASK` | 用户明确启动即可运行，不受自动节点限制 |
| 自动节点任务 | `CHATGPT_SCHEDULED_FORMAL_ANALYSIS` | `AUTOMATIC_SCHEDULED_TASK` | 仅受current main正式日历、交易日和节点限制 |

入口身份必须根据实际触发方式确定：

- 用户在对话窗口明确要求执行，不能改判为自动任务；
- 用户在任务界面点击运行，不能改判为自动任务；
- 只有系统无人手动、按自动化日历自行唤起，才是自动任务；
- 不能用任务名称、当前时间、旧Prompt、历史运行或行情快照时间猜测入口；
- 无法证明是系统自动唤起时，不得套用自动节点SKIP规则。

每次运行都必须生成新的：

`task_id + task_run_id + occurrence_id + parent_request_id + request_id + market_date`

对话入口可以使用 `conversation_` 前缀的 occurrence；手动任务使用 `manual-scheduled-` 前缀；自动任务使用 `scheduled_` 前缀。任何入口都不得复用历史身份或创建第二请求。

## 三、唯一生产链

三个入口统一执行以下链条：

`实际触发方式`
→ `本次唯一运行身份`
→ `current main正式规则与当前事实`
→ `完整业务判断`
→ `唯一FINAL_CONTENT`
→ `用户窗口输出FINAL_CONTENT`
→ `STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW`
→ `canonical state-sync owner`
→ `唯一events/reviews/formal_<occurrence_id>.json`
→ `既有decision-notification`
→ `notification_center`
→ `PushPlus/微信原文投递`

三入口统一使用以下请求合同：

- `request_type=STATE_SYNC_ONLY`
- `formal_fact_type=FORMAL_SCHEDULED_REVIEW`
- `source=CHATGPT_SCHEDULED_REVIEW_ACTOR`
- `trigger_source` 保存实际三入口来源；
- `entry_mode` 保存实际三入口身份；
- `interaction_scenario=FORMAL_DECISION`
- `presentation_binding.report_type=ETF_FORMAL_DECISION`
- `presentation_binding.task_id`
- `presentation_binding.task_run_id`
- `presentation_binding.report_id`
- `presentation_binding.full_content`
- `presentation_binding.content_hash`
- `presentation_binding.market_date`

## 四、已删除的旧生产链

以下路径不再属于三个入口中的任何一个：

`MARKET_QUOTE_REFRESH`
→ `query_context/DWP/formal_reply_gate`
→ `BUSINESS_DECISION_SOURCE/BDS`
→ `FORMAL_DECISION_COMPLETION`

具体禁止：

- 不得用 `MARKET_QUOTE_REFRESH` 作为Formal Decision首跳；
- 不得等待、读取或复用历史 `formal_reply_gate`；
- 不得把历史或空的 `query_context`、DWP、父请求当作本次前置门禁；
- 不得写入或回读 BDS 作为三个入口的Formal生产步骤；
- 不得生成 `FORMAL_DECISION_COMPLETION`；
- 不得让 BDS、market-snapshot 普通行情请求或历史completion唤醒通知；
- 不得保留“对话窗口一套链、定时任务另一套链”的解释。

`MARKET_QUOTE_REFRESH` 如被其他非Formal行情能力使用，仍只属于一般行情数据能力，不得被三个Formal入口调用或冒充Formal Decision请求。

## 五、当前事实与业务判断

先读取 `ETF_SYSTEM_INDEX.md`，再按INDEX读取MASTER、数据规范、账户和持仓事实、当前正式行情、风险、Discovery、观察ETF、机会ETF、通知合同和生产变更合同。

历史聊天、旧Prompt、旧请求、旧报告、旧gate、旧DWP和旧正文只能作为线索，不得作为本次正式事实。

当前事实存在合法降级时，可以继续完成判断，但必须在正文中明确写出：

- 哪些事实已确认；
- 哪些事实降级或缺失；
- 对业务判断造成什么影响；
- 哪些结论因此降低置信度。

只有current main明确规定的非交易日、自动入口非适用节点或明确无法形成当前事实的终止条件，才允许不形成正式判断。手动入口不能因为不在自动节点而SKIP。

## 六、正文合同

完成业务判断后只冻结一份 `FINAL_CONTENT`。

正文必须：

- 首行固定为“【ETF正式决策】YYYY-MM-DD HH:MM 正式判断”；
- 使用本次实际判断时间，不使用任务计划时间；
- 完整覆盖本协议第一节的业务问题；
- 逐只覆盖实际持仓；
- 区分Discovery、观察ETF、机会ETF和持仓ETF；
- 对资金动作写明金额、资格、依据和改变条件；
- 明确“不得自动下单、用户人工确认”；
- 不展示request_id、路径、commit、workflow/run ID、内部writer、provider日志或内部状态；
- 不因通知、provider、workflow或持久化状态而缩短、摘要或改写。

用户窗口输出的正文、completion、review event、REPORT、notification和PushPlus payload必须逐字一致，均只能透传同一份 `FINAL_CONTENT`。

## 七、后置交接和通知

用户窗口输出唯一 `FINAL_CONTENT` 后，在同一次运行中只提交一次统一 Scheduled Review 请求，并立即回读：

1. `requests/live_snapshot/<request_id>.json`；
2. 当前 `events/reviews/formal_<occurrence_id>.json`；
3. 当前REPORT；
4. 当前`notification_id`；
5. 当前PushPlus provider result。

Actor不得直接写event、REPORT、notification或PushPlus。通知只能由当前唯一review event进入既有notification_center和guarded sender。

通知失败不撤回用户已经看到的正文，但必须如实记录。没有当前notification_id和provider证据时，不得声称当前通知成功；不得扫描历史对象、补发历史正文、创建第二正文、第二请求、第二event或第二通知链。

## 八、完成判定

一次完整成功的Formal Decision必须同时具备：

- 正确入口身份；
- 本次唯一运行身份；
- 当前事实已读取；
- 完整业务判断已形成；
- 唯一FINAL_CONTENT已冻结并输出；
- 当前STATE_SYNC_ONLY请求已写入并回读；
- 当前review event已形成；
- 当前REPORT已形成；
- 当前notification_id已形成；
- 当前provider result已回读。

这些状态必须分别记录，不能互相替代：

- workflow成功不等于event成功；
- event成功不等于通知成功；
- provider成功不等于用户实际收到；
- 用户正文已输出不等于后置通知已闭环。

本协议不新增workflow、producer、checker、state、writer、provider、交易权限或第三个ETF任务；只将三个Formal入口收敛到同一条既有Scheduled Review/REPORT/notification_center/PushPlus生产链。
