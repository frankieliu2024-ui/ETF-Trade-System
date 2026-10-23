# Formal Decision执行与用户回复协议

版本：统一三入口单链 V5  
适用范围：每一次【ETF正式决策】的对话窗口运行、任务界面手动运行和系统日历自动运行。

本协议是 Formal Decision 的唯一现行执行合同。交易规则、数据资格、通知服务商优先级和生产变更规则分别服从 INDEX 路由的 MASTER、数据规范、主动通知协议和生产治理协议。本协议不创建新的 workflow、producer、checker、state、writer、交易权限或通知链。

## 一、唯一目标与边界

每次运行必须基于本次唯一请求绑定的最新正式事实，形成一份完整、可审计且对象级覆盖的正式 ETF 管理判断。

判断至少覆盖：

- 风险许可、市场状态和资本环境；
- 海外与跨市场、A股内部反馈和三层监测；
- 全市场 Discovery 对象；
- 全部观察 ETF；
- 当前机会 ETF；
- 全部实际持仓及每只持仓的继续持有、追加、减持或退出判断；
- 现金、可部署现金、可释放资本和资金缺口；
- Trial/Confirm 资格、建议动作、实际新增部署和实际执行结果；
- 关键风险、改变判断的条件、信息不足、置信度和最可能出错的地方。

不得自动下单、自动调仓或改变交易权限。所有证券交易均由用户人工确认和执行。

不得消费、改写或替代【ETF系统复核】和【ETF交易复盘】的业务对象。

## 二、入口和唯一请求

入口必须依据真实触发方式识别：

| 入口 | source | entry_mode |
|---|---|---|
| 对话窗口手动运行 | CHATGPT_MANUAL_FORMAL_ANALYSIS | MANUAL_CONVERSATION |
| 任务界面手动运行 | CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS | MANUAL_SCHEDULED_TASK |
| 系统日历自动运行 | CHATGPT_SCHEDULED_FORMAL_ANALYSIS | AUTOMATIC_SCHEDULED_TASK |

task_id固定为“ETF正式决策”，interaction_scenario固定为“FORMAL_DECISION”。

手动入口必须为本次运行建立全新的唯一父请求；自动入口必须消费 current main 已为本次自动节点建立的唯一请求，不得自行创建第二请求。

父请求必须绑定：

- task_id、task_run_id、occurrence_id；
- parent_request_id、request_id；
- market_date、requested_at_beijing、runtime_now；
- source、entry_mode；
- request_type=MARKET_QUOTE_REFRESH；
- intent.query_intent=FORMAL_INTRADAY_ANALYSIS；
- force_refresh=true；
- require_post_request_snapshot=true；
- reuse_inflight_refresh=true；
- duplicate_equivalent_refresh_forbidden=true。

请求写入后必须回读同一路径并核对全部身份字段。请求建立不等于事实生成、正文完成、事件形成或通知完成。

## 三、同一请求的事实生产与等待

所有市场、账户、成交、风险、生命周期、Discovery、观察 ETF、机会 ETF和实际持仓事实，必须绑定同一父请求和同一 request-bound 快照。

必须读取同一请求对应的：

- formal_reply_gate；
- query_context；
- decision_context；
- decision_work_package；
- problem_graph；
- request-bound 市场快照；
- 账户与成交事实；
- 市场快照生产状态。

不得调用无请求参数的“最新请求”或共享目录选择器，不得切换历史请求。

事实正在生成、门禁仍在排队/运行/构建、上下文未发布或身份未匹配时，必须沿同一请求轮询。每次间隔约15秒，最长等待6分钟；每次必须重新回读同一请求的生产状态、门禁、上下文和事实。

等待期间不得输出终止性业务结论。只有同一请求达到合法准入、明确终止失败、current main明确不存在继续路径，或达到有界等待上限，才可结束等待。

达到上限时必须记录本次请求身份、最后回读时间、最后状态、阻断层级、是否仍有同一请求继续运行证据、是否进入事实生产链，以及是否仍存在旧门禁。不得使用旧事实补齐本次请求。

## 四、正式判断准入

只有以下条件全部满足，才允许生成正式判断：

1. 入口和运行身份正确；
2. 本次请求与 market_date 一致；
3. 本次请求已进入 current main 事实生产链；
4. 门禁允许正式判断；
5. 门禁全部身份字段与本次请求一致；
6. query_context 和 decision_context 绑定本次请求；
7. query_context 中 request_id、source、entry_mode、requested_at_beijing 和 market_date 均非空且正确；
8. request-bound 市场、账户和成交事实可读；
9. decision_work_package 和 problem_graph 属于本次请求；
10. 数据时点、PIT和新鲜度符合要求。

门禁已绑定但事实包身份缺失时，必须记录“门禁已绑定但事实包未绑定”，不得进入正式判断。

READY只表示可以开始业务判断，不表示正文、完成记录、事件、报告、通知或用户收到已经完成。

## 五、唯一 FINAL_CONTENT

准入后必须立即锁定本次 request-bound 事实，生成并冻结唯一 FINAL_CONTENT，不得停在“已达到准入状态”。

正文首行必须严格为：

【ETF正式决策】YYYY-MM-DD HH:MM 正式判断

正文必须使用自然中文，并逐只覆盖全部实际持仓、全部观察 ETF、全部机会 ETF及 Discovery 对象。必须严格区分事实、判断、资格、建议动作、实际新增部署和实际执行结果。

正文必须明确：

- 数据时点和事实性质；
- 每只实际持仓的管理结论、理由、替代比较、可释放资本和改变条件；
- 如无交易条件：新增部署为0元，实际释放为0份，现金和持仓结构维持不变；
- 所有建议仅为管理建议，不自动下单、不自动调仓、不改变交易权限。

证券首次出现使用“名称（六位代码）”格式，同一对象全文保持一致。

用户正文不得出现请求编号、路径、提交信息、内部状态、workflow、job、完成记录、报告、通知中心、服务商、代码字段或内部日志。

冻结前必须完成事实边界、时点、对象覆盖、建议与执行区分、结构排版和内部字段泄露检查。冻结后不得摘要、缩短、改写、拼接、重新编号或重新分析。

## 六、唯一生产交接链

FINAL_CONTENT冻结后，必须调用 current main 登记的 ETF 正式决策 Actor，并继续沿同一请求、同一事实快照和同一冻结正文交接：

唯一父请求
→ request-bound事实
→ 合法准入
→ 唯一 FINAL_CONTENT
→ ETF正式决策 Actor
→ current main 统一完成记录生产者
→ 正式事件
→ 正式报告
→ 通知记录
→ 服务商投递结果
→ 用户收到状态。

Actor不得创建第二请求、第二正文、第二完成记录、第二事件、第二报告或第二通知链；不得直接手写事件、报告、通知或调用服务商。

完成记录必须由统一生产者生成并回读，至少核对：

- task_id、task_run_id、occurrence_id、request_id；
- parent_request_id、source、entry_mode、market_date；
- interaction_scenario、report_type=ETF_FORMAL_DECISION；
- 完整 FINAL_CONTENT 和 content_hash。

完成记录形成不等于事件、报告、通知或用户收到形成。

## 七、事件、报告与通知回读

完成记录成功写入并回读后，必须沿 current main 既有正式决策生产路径继续处理。

每层必须绑定本次父请求、occurrence_id、decision_id、正文和正文指纹，并分别回读：

- 完成记录；
- 正式事件；
- 正式报告；
- 通知记录和通知编号；
- 服务商投递结果；
- 用户收到状态。

每次轮询约15秒，单层最长6分钟。不得扫描历史对象、使用旧正文或旧通知补齐本次运行。

必须严格区分：

- 完成记录形成；
- 正式事件形成；
- 正式报告形成；
- 通知记录形成；
- 服务商投递成功；
- 用户实际收到。

workflow成功不等于上述任何后续状态成功；服务商成功不等于用户实际收到。

## 八、失败、恢复和最终回复

任一层失败，必须沿同一请求、同一冻结 FINAL_CONTENT 和同一已形成的完成记录继续恢复，不得重新分析或创建第二链。

如果事实未达到合法准入且达到合法终止条件，只能说明本次未形成正式判断，并说明最后完成层级和事实阻断。

如果 FINAL_CONTENT 已冻结，即使后置交接失败，也必须输出同一份完整正文，并在正文后说明：

- 已完成层级；
- 未完成层级；
- 失败原因是否确认；
- 是否需要沿同一请求继续恢复；
- 用户收到状态。

不得把技术失败改写成业务结论，不得把完成记录、事件、报告、服务商成功或用户收到混写为同一状态。

## 九、历史兼容对象

历史 BDS、旧完成记录、旧事件、旧报告和旧通知可以保留为审计档案，但不得作为当前 Formal Decision 的事实、准入依据、正文来源或生产输入。

当前生产代码、workflow、测试和治理检查不得创建、读取或依赖 BDS。历史档案不属于当前生产链。

本协议不新增生产链；它只约束当前已有的 Formal Decision Actor、统一完成记录生产者、正式事件、报告和通知链。

