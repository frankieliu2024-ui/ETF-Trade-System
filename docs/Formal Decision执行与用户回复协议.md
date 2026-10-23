# Formal Decision执行与用户回复协议

版本：统一三入口单链 V4  
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
→ `current main正式规则与本次request-bound事实`
→ `完整业务判断`
→ `唯一FINAL_CONTENT`
→ `BUSINESS_DECISION_SOURCE/BDS + SCHEDULED_REVIEW_COMPLETION 原子持久化`
→ `显式通知dispatch`
→ `canonical owner`
→ `唯一events/reviews/formal_<occurrence_id>.json`
→ `REPORT/notification_center/PushPlus/微信原文投递回读`
→ `用户窗口输出FINAL_CONTENT`

三个入口的业务语义统一，但入口身份必须原样保留。正式决策不得使用旧的独立 Formal completion；不得再使用旧的独立 Formal completion 生产链。它必须使用统一 Scheduled Review completion，但不得让系统复核或交易复盘的业务事实冒充正式决策。

正式决策必须依次形成并绑定：

- 唯一 `parent_request_id` 和 `request_id`；
- 本次请求对应的 request-bound snapshot、DWP、problem_graph；
- 唯一 `decision_id`；
- 唯一冻结 `FINAL_CONTENT`；
- 唯一 BDS；
- 唯一 Scheduled Review completion（`report_type=ETF_FORMAL_DECISION`）；
- 唯一 `occurrence_id` 和 review event；
- 唯一 REPORT、notification_id和provider result。

`BUSINESS_DECISION_SOURCE` 只承载本次业务判断及正文冻结，不直接写入review event、REPORT或notification。
`SCHEDULED_REVIEW_COMPLETION` 只承载同一父请求的后置交接，不重新生产判断，不重新生成正文，不读取历史BDS或历史completion；Formal Decision 通过 `formal_fact_type=FORMAL_POST_CLOSE_REVIEW` 和 `report_type=ETF_FORMAL_DECISION` 保留业务身份。

## 四、正式决策请求与后置交接合同

正式决策首跳必须使用当前正式请求合同：

- `request_type=MARKET_QUOTE_REFRESH`
- `source` 按三入口分别为 `CHATGPT_MANUAL_FORMAL_ANALYSIS`、`CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS` 或 `CHATGPT_SCHEDULED_FORMAL_ANALYSIS`
- `entry_mode` 分别为 `MANUAL_CONVERSATION`、`MANUAL_SCHEDULED_TASK 或 `AUTOMATIC_SCHEDULED_TASK`
- `force_refresh=true`
- `require_post_request_snapshot=true`
- `reuse_inflight_refresh=true`
- `duplicate_equivalent_refresh_forbidden=true`

READY 后，必须先完成业务判断并冻结唯一 `FINAL_CONTENT`，随后在同一运行内完成BDS、统一 `SCHEDULED_REVIEW_COMPLETION` ingress、显式通知dispatch及后续event、REPORT、notification和provider回读。交接成功或明确到达最后失败层级后，才向用户窗口输出此前冻结的同一份完整正文，并附真实交接状态；后置交接不得重新分析、改写或生成第二份正文，失败或超时不得无限等待：

1. 使用同一父请求、同一DWP、同一request-bound snapshot和同一`decision_id`，形成 `BUSINESS_DECISION_SOURCE` envelope；
2. 将BDS写入并回读 `requests/live_snapshot/<parent_request_id>__business_decision_source.json`；
3. 使用该BDS路径、同一`decision_id`和同一`content_hash`，形成统一的 `SCHEDULED_REVIEW_COMPLETION` envelope；
4. 将completion写入并回读，文件名必须精确等于completion自身`request_id`;
5. 在同一handoff中显式调用既有 `decision-notification.workflow_dispatch(formal_completion_path=<当前completion路径>)`；
6. 回读当前 `events/reviews/formal_<occurrence_id>.json`、REPORT、notification_id和provider result。

任何后置阶段都不得重新分析、改写或生成第二份正文；后置交接失败不得创建第二请求，但必须继续执行至成功或记录明确最后失败层级，随后输出同一份冻结正文。不得因等待不可取得的provider或用户收件确认而无限等待。没有当前completion对应的event，不得声称正式决策事件已形成；没有当前notification_id和provider结果，不得声称主动通知成功。


## 四点一、同一请求的行情生产等待与二次回读

正式决策请求建立后，消费侧不得依据一次中间读取直接终止。必须沿同一父请求、同一请求编号和同一请求文件等待行情生产链进入终态，并在终态后重新回读刷新门、请求绑定事实和正式回复冻结状态。

等待规则如下：

1. 每次检查只能读取同一请求的结果；不得创建第二请求、重复补采、第二事实链或历史回退。
2. 若刷新链仍为排队、运行中、构建中或请求绑定事实尚未发布，必须继续等待；不得向用户输出“本次未形成正式判断”。
3. 若刷新门为READY，且正式回复冻结状态为可冻结、阻塞项为空、请求编号一致，必须继续完成业务判断，不得停在“行情仍在生成”。
4. 若当前市场阶段允许合法复用最近有效收盘事实，刷新门返回READY时，应以该终态为准；不得机械要求产生不存在的盘中快照。
5. 等待达到入口规定的有限时限仍未取得终态时，必须记录“同一请求等待超时，最终状态未取得”，并保留同一请求供后续恢复；不得把超时改写为行情供应商失败或确定性根因。
6. 只有同一请求明确进入终止失败，或已确认不存在合法继续路径，才允许不形成正式判断。

该等待和二次回读属于Formal Decision消费合同，不新增producer、workflow、state或通知链；正式判断仍必须在同一请求的事实可消费后完成，并继续使用本协议既有BDS、completion和通知交接。


## 五、已删除的旧生产链

以下路径不得作为正式决策生产链：

旧 `STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW` 或独立 `FORMAL_DECISION_COMPLETION`
→ 第二个 Formal owner
→ Formal Decision event

也不得：

- 把系统复核或交易复盘的完成请求当作Formal Decision；
- 以旧 `formal_reply_gate`、旧DWP、旧BDS、旧completion或历史正文完成本轮判断；
- 直接手写 `events/reviews/formal_*.json`、REPORT或notification；
- 让BDS、普通market snapshot push或历史completion被动替代显式workflow dispatch；
- 在三个入口之间创建不同的业务判断、正文、event或通知链。

`STATE_SYNC_ONLY` 仍可被系统复核、交易复盘等既有任务使用，但不属于本次Formal Decision主链。

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

FINAL_CONTENT 冻结后，先在同一运行上下文中以原子 BDS+completion 提交并完成显式通知dispatch，继续完成 canonical event、REPORT 和通知，全部复用该正文；交接成功或明确记录最后失败层级后，再向用户输出此前冻结的同一份 `FINAL_CONTENT`。后置交接失败不得改写或生成第二份正文，且不得无限等待：

1. BDS与统一 Scheduled Review completion 以同一 Git tree/commit 原子写入并分别回读；
3. 显式调用既有 `decision-notification` workflow的 `workflow_dispatch(formal_completion_path=...)`；
4. 回读当前 `events/reviews/formal_<occurrence_id>.json`；
5. 回读当前REPORT、notification_id和provider result。

Actor不得直接写event、REPORT、notification或PushPlus。通知只能由当前唯一review event进入既有notification_center和guarded sender。通知失败不撤回用户已经看到的正文，但必须如实记录。不得扫描历史对象、补发历史正文或回退到历史对象，不得创建第二正文、第二请求、第二event或第二通知链。

## 八、完成判定

一次完整成功的Formal Decision必须同时具备：

- 正确入口身份；
- 本次唯一运行身份；
- 当前事实已读取；
- 本次request-bound事实、DWP和problem_graph一致；
- 完整业务判断已形成；
- 唯一FINAL_CONTENT已冻结并输出；
- 当前BDS已写入并回读；
- 当前SCHEDULED_REVIEW_COMPLETION已写入并回读，且 `report_type=ETF_FORMAL_DECISION`；
- 当前review event已形成；
- 当前REPORT已形成；
- 当前notification_id已形成；
- 当前provider result已回读。

这些状态必须分别记录，不能互相替代：

- workflow成功不等于event成功；
- event成功不等于通知成功；
- provider成功不等于用户实际收到；
- 用户正文已输出不等于后置通知已闭环。

本协议不新增workflow、producer、checker、state、writer、provider、交易权限或第三个ETF任务；只将三个Formal入口收敛到当前已有的BDS、Formal Decision completion、canonical event、REPORT、notification_center和PushPlus生产链。

### Formal正文排版合同

FINAL_CONTENT冻结前必须通过统一的Formal正文结构校验：一级章节标题独占一行；单独只有一段的说明、置信度或“最可能出错的地方”不得机械添加“1.”；存在两个及以上并列项目时，一级编号必须从1开始连续递增，并在项目之间保留空行；二级对象列表只能在明确的一级项目下使用，编号同样从1开始连续递增。全市场发现和观察ETF的概述段不属于候选对象，不得占用候选编号；证券名称、代码和候选数量必须与正文实际列举数量一致。BDS、completion、canonical event、REPORT和PushPlus必须逐字复用通过该校验的同一份冻结正文，不得在后置链重新排版或重新编号。


## 八、 大型 request-bound 事实的统一读取合同

当 `data/state/query_context.json` 等 request-bound 事实文件超过 1 MB 时，Formal Decision 消费侧必须使用 GitHub API Contents 或 blob 读取；不得使用会返回空载荷的普通文件或 raw 读取路径。读取后必须在同一 current main 提交上校验实际字节数、SHA-256、JSON 可解析性、`decision_fact_pack.trigger.request_id`、请求绑定快照以及 `decision_work_package.problem_graph`。大型文件的连接器空载荷只能归类为读取通道不适配，不能归类为文件缺失、请求终止或新的 Formal Decision 失败。READY 只有在上述 request-bound 内容实际可消费时才成立；读取通道切换不得创建第二请求、第二状态或平行生产链。
