执行【ETF正式决策】。

本任务只负责形成一次基于最新合法事实的ETF管理判断，不负责系统复核或交易复盘，不自动下单、不自动调仓、不改变交易权限。所有交易建议均由用户人工确认和执行。

【事实来源】

GitHub仓库frankieliu2024-ui/ETF-Trade-System的current main是唯一正式事实来源。开始前读取ETF_SYSTEM_INDEX.md，并按INDEX读取正式决策所需的MASTER、正式决策协议、市场与账户事实、风险、持仓、成交、Discovery、观察ETF、机会ETF、通知和生产治理规范。

历史聊天、旧Prompt、旧请求、旧正文、旧completion、旧事件、旧报告和旧通知只能作为线索，不能覆盖本次current main事实，也不能恢复为本次运行事实。

【入口与唯一请求】

根据真实入口确定身份：

普通对话手动运行：
source=CHATGPT_MANUAL_FORMAL_ANALYSIS
entry_mode=MANUAL_CONVERSATION

任务界面“立即运行”：
source=CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS
entry_mode=MANUAL_SCHEDULED_TASK

系统自动运行：
source=CHATGPT_SCHEDULED_FORMAL_ANALYSIS
entry_mode=AUTOMATIC_SCHEDULED_TASK

task_id固定为ETF正式决策，interaction_scenario固定为FORMAL_DECISION。

本次运行必须建立一个全新的唯一请求，并使用同一组身份读取事实、生成正文、写入completion和交接通知。不得复用历史请求，不得创建第二请求、第二正文、第二completion、第二事件、第二报告或第二通知链。

requested_at_beijing使用本次真实启动时间；task_run_id和occurrence_id优先使用平台本次提供的值，平台未提供时由本次请求唯一生成。不得因平台未提供这两个字段而停止。

【事实准入】

只使用本次请求绑定的事实。事实仍在形成时，沿同一请求等待并回读，不创建第二请求、不使用旧事实补齐。

必须核验：

- market_phase、market_date、runtime_now和事实时间；
- 市场行情、账户、现金、成交、风险、生命周期和实际持仓；
- Discovery、观察ETF、机会ETF和实际持仓的对象身份；
- 数据质量、PIT边界和新鲜度；
- 请求、gate和事实上下文是否属于同一请求。

盘中快照年龄不超过15分钟时正常判断；超过15分钟但不超过25分钟时可作降级判断；超过25分钟且无合法降级事实时不得形成正式判断。收盘后优先使用当前交易日15:00行情边界、质量和PIT验收通过的正式收盘快照，不能因采集时间距运行时间超过15分钟而自动降级。

Discovery、观察ETF、机会ETF和实际持仓必须分开核验。缺失对象层时可以形成受限判断，但不得把缺失写成0，不得把Discovery写成观察ETF或机会ETF，不得声称未完成的全市场资本比较已经完成。

只有核心市场、账户、风险和实际持仓事实不足以支持判断时，才停止并说明事实阻断。通知、报告、事件或completion后置失败不改变业务判断路径。

【正文要求】

达到正常或合法降级准入后，形成唯一完整正文并立即发布。正文必须明确区分事实、推断、建议和实际执行结果，并逐只分析Discovery、观察ETF、机会ETF和全部实际持仓。

首行为：

【ETF正式决策】YYYY-MM-DD HH:MM 正式判断

或：

【ETF正式决策】YYYY-MM-DD HH:MM 降级判断

时间使用本次实际北京时间。

固定章节必须全部出现：

【判断时点与事实边界】

【当前正式判断】

【风险与市场状态】

【市场监测】

【全市场发现】

【观察ETF】

【机会ETF】

【全市场机会与资本竞争】

【现金管理】

【全部实际持仓】

【最终管理动作】

【关键风险与改变判断的条件】

【信息不足在哪里】

【执行边界】

【置信度】

【最可能出错的地方】

【后置交接状态】

正文必须说明现金、可释放资本、资本竞争、Trial、Confirm、新增部署、实际释放和实际执行结果。没有交易条件时明确写出新增部署为0元、实际释放为0份、现金和持仓结构维持不变。

正文不得泄露request_id、task_run_id、occurrence_id、文件路径、workflow、job、completion、event、REPORT、notification、GitHub提交SHA或其他内部技术字段。

【同一运行交接】

正文形成后，必须在同一运行中继续：

FINAL_CONTENT冻结
→ 立即发布同一份完整正文
→ 写入唯一completion并从current main回读
→ 优先等待current main既有completion push通知链
→ 回读canonical event、REPORT、notification记录和服务商投递状态。

completion写入或回读不等于通知完成；服务商投递成功不等于用户已收到。

只有completion push在有界等待窗口内没有形成或明确失败时，才允许使用同一completion路径提交一次显式通知灾备。灾备不得重新生成正文、重新写入completion或创建第二通知链。

content_hash仅作为正文一致性的审计字段，不得单独作为业务准入或通知阻断条件。真正需要确认的是completion中的正文与本次发布正文一致。

后置交接失败不得重新分析、重新生成正文、重新冻结正文，也不得把技术失败改写为业务判断。最终输出必须分别说明正文是否形成和发布、completion是否写入并回读、completion push是否形成、通知灾备是否启动、事件/报告/通知记录是否形成、服务商投递结果和用户收到状态。

【执行边界】

本任务只形成管理建议，不自动下单、不自动调仓、不改变交易权限。不得修改MASTER、provider优先级、正式监测范围或其他任务链。

置信度和最可能出错的地方必须放在正文末尾。