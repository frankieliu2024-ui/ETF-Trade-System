# Formal Decision 定时任务重建合同 V2

版本：V2.1  
适用范围：仅限【ETF正式决策】定时任务。  
唯一规则来源：本文件由 `ETF_SYSTEM_INDEX.md` 路由，交易权限、风险阈值、观察/机会/持仓定义仍分别服从 MASTER、数据规范和 Formal Decision 执行协议。

## 一、与两个稳定任务的边界

【ETF系统复核】和【ETF交易复盘】仅作为只读对照对象。不得修改其任务、调度、代码、协议、状态、completion、REPORT、notification 或 PushPlus 链。本任务复用 current main 已登记的 Formal Decision canonical owner、BUSINESS_DECISION_SOURCE、completion ingress 和共享 decision-notification，不复制第二套生产链。

## 二、两个合法入口

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

入口先建立并回读 `requests/live_snapshot/<request_id>.json`，请求类型为 `MARKET_QUOTE_REFRESH`，意图为 `FORMAL_INTRADAY_ANALYSIS`，并绑定 `force_refresh`、`require_post_request_snapshot`、PIT/freshness、当前账户事实、三层监测、Discovery、观察ETF、机会ETF和DWP/problem_graph。WAIT、queued、building、in-flight、旧gate、空读或派生状态暂未追上时，只能沿同一父请求静默继续；不得把它们解释为失败，也不得创建第二请求。只有当前请求明确 READY，或 canonical producer 明确 terminal failure 且不存在合法继续路径，才可结束等待。

## 五、READY后的业务与正文

READY 后必须完成同一DWP的全部必答问题：风险许可；海外与跨市场；A股内部反馈；ETF与资本机会；全市场机会发现；全部观察ETF；当前机会ETF；全部实际持仓；每只持仓是否追加；现金、可释放资本、资金缺口；Trial/Confirm资格；实际部署金额；最终动作；关键风险；改变动作的条件及DWP登记的其他问题。

只冻结一份 `FINAL_CONTENT`，严格使用 Formal Decision 固定标题、章节顺序、编号、证券“名称（六位代码）”格式和用户可见边界。观察ETF只有同一DWP六项 thesis 字段完整时才可 RETAIN；缺失时不得补造，只能按当前事实 EXIT，或在DWP允许时提交完整当前 ADMIT thesis。不得自动下单。

## 六、后置投影

用户窗口必须先输出完整且冻结的 `FINAL_CONTENT`。随后同一运行继续：

1. 使用 BUSINESS_DECISION_SOURCE 写入并回读当前请求、身份、request-bound snapshot、answers、完整正文和 content_hash。
2. 使用唯一 Formal Decision completion ingress 生成、写入并回读 `<parent_request_id>__formal_decision_completion.json`。
3. 立即对当前 completion 路径调用既有 decision-notification workflow 的 `workflow_dispatch(formal_completion_path=...)`；没有插件 dispatch 写调用时，使用授权云浏览器的 Actions → Run workflow 表单完成同一 dispatch，并回读真实 run。
4. 只由 canonical owner 形成 `events/reviews/formal_<occurrence_id>.json`，再进入 notification_center 和 PushPlus。不得手写 event、REPORT 或 notification，不得直接调用 PushPlus，不得扫描历史对象或通过 BDS/market-snapshot 被动唤醒。

BDS写入、BDS回读、completion、event、notification_id、PushPlus provider result和用户实际收到必须分别记录。缺少当前 notification_id 对应的 provider 成功证据时只能记为 `EXTERNAL_SEND_UNVERIFIED`。

## 七、验收

代码验收必须覆盖三入口身份、四节点cron、自动身份缺失 fail-closed、occurrence幂等和观察thesis前置合同。真实目标路径验收必须使用全新请求，核对：

`入口 → request → request-bound facts → READY → FINAL_CONTENT → BDS → completion → workflow_dispatch → event → notification_id → provider result`

CI通过不等于真实通知成功；任务建立、运行已发起、workflow success或provider success均不等于用户实际收到。

## 八、手动启动定时任务的运行身份建立（V2.1）

“用户手动启动本自动化”与“系统按日历自动唤起”是两个不同入口。平台没有向模型暴露自动化运行凭据时，只能阻断自动入口；不得把该限制套用到用户手动启动入口。

对 `MANUAL_SCHEDULED_TASK`：

1. 用户在本自动化任务的运行界面明确发起本次运行，即是本次手动触发事实；不得因当前时间不在自动节点而 SKIP。
2. `task_id` 使用当前任务的固定正式名称“ETF正式决策”；
3. `task_run_id` 与 `occurrence_id` 必须在本次手动运行首次创建请求时一次性生成，格式为 `manual-scheduled-YYYYMMDDTHHMMSS+0800-<短随机后缀>`，并写入同一请求。该身份是本次手动 ingress 新建的当前运行身份，不得使用历史值，不得伪造为自动运行身份，也不得在后续步骤重新生成；
4. `parent_request_id`、`request_id` 和 `market_date` 从同一请求 ingress 绑定并持续复用；
5. 生成身份后必须立即写入并回读 `requests/live_snapshot/<request_id>.json`，后续所有事实、DWP、BDS、completion、event 和通知只能绑定这一组身份；
6. 若同一手动运行已经取得当前 `task_run_id/occurrence_id`，只能继续消费该父请求；不得因后续读取不到自动化平台元数据而重新判定为未知、创建第二请求或暂停任务。

对 `AUTOMATIC_SCHEDULED_TASK`，第三节的运行时注入和缺失即 fail-closed 规则保持不变；不得用本节的手动身份生成规则补齐自动入口。此次 V2.1 只修复手动启动入口的身份建立，不改变两个稳定任务、正式自动节点、交易规则或通知链。
