# Formal Decision执行与用户回复协议

## 1. 目的与范围

本协议只规定 Formal Decision 的请求消费、业务回复时序和用户可见边界。交易规则以 `ETF规则_MASTER.md` 为准，事实、PIT、freshness 和三层监测以数据规范为准，代码与生产变更以生产变更协议为准。本协议不新增 workflow、state、producer、checker、writer、provider 或第二决策链。

适用范围：用户发起正式决策、要求重新判断、询问是否买卖/调仓、现在怎么办或盘中分析。System Review、Formal Review、Technical Audit 和普通行情问答不适用本协议。

## 1.1 统一核心执行合同（最高优先级）

本节是三个正式决策入口共同遵守的唯一执行总合同。后文如存在重复、简写或解释差异，以本节为准；后文只补充本节未定义的入口字段、业务内容和展示细节，不得改变本节顺序。

### 1.1.1 三个入口适配

三个入口只负责声明入口身份并创建或恢复自己的当前请求，READY 后全部进入同一核心链：

|入口|source|entry_mode|
|-|-|-|
|对话窗口|'CHATGPT_MANUAL_FORMAL_ANALYSIS'|'MANUAL_CONVERSATION'|
|手动定时任务|'CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS'|'MANUAL_SCHEDULED_TASK'|
|自动定时任务|'CHATGPT_SCHEDULED_FORMAL_ANALYSIS'|'AUTOMATIC_SCHEDULED_TASK'|

三者必须分别保留当前 request_id、parent_request_id、task_id、task_run_id、occurrence_id 和 market_date。入口不得互相冒充、消费其他入口的历史请求，或为同一业务另建 producer、completion、REPORT、notification 或通知链。自动任务仍只在既有正式节点执行；非交易日或不适用节点只 SKIP，不创建请求、不消费旧请求、不生成伪报告。

### 1.1.2 唯一业务主链

所有入口严格按以下顺序执行：

当前入口 → 当前唯一 parent request → request-bound snapshot → query_context → DWP/problem graph → formal_reply_gate → READY → 业务判断 → 冻结唯一 FINAL_CONTENT → 用户可见回复（同一 FINAL_CONTENT） → BUSINESS_DECISION_SOURCE 写入并回读 → Formal Decision completion ingress → 当前 events/reviews/formal_<occurrence_id>.json → decision-notification → notification_center → 当前 notification → PushPlus provider result

用户正文是 READY 后的首个可见动作。BDS、completion、canonical review event 和通知均为后置投影，不得阻塞、缩短、改写或撤回 FINAL_CONTENT；但在同一执行回合内应继续提交并回读这些后置对象。后置投影失败只能如实记录，不能重建正文、创建第二请求或回退到历史对象。

其中：

- force_refresh=true 或 require_post_request_snapshot=true 时，本请求创建后生成并绑定的 snapshot、Discovery、query_context、DWP 和 decision context 属于当前请求合法事实；不得因为生成时间晚于请求创建时间而排除；
- WAIT 期间只能继续消费同一 parent request；不得创建第二请求、复用旧事实或输出中间失败说明；
- 当前请求 formal_reply_gate=READY、reply_freezable=true 且身份匹配时，立即完成业务判断；不得等待历史 completion、历史 REPORT、notification、PushPlus 或系统健康状态来决定是否开始判断；冻结 FINAL_CONTENT 后，必须完成当前请求的BDS交接和既有canonical Formal Decision event形成，但不得以BDS直通知或历史对象替代当前event；
- 当前请求未 READY 时，不得使用旧 gate、全局状态或历史 DWP 代替；只有明确 terminal failure 才能终止。

### 1.1.3 唯一正文合同

READY 后只生成一份 FINAL_CONTENT。它必须同时满足：

- 用户正式回复正文 = FINAL_CONTENT；
- presentation_binding.full_content = FINAL_CONTENT；
- completion、REPORT、notification 和 PushPlus payload 的正文逐字等于 FINAL_CONTENT；
- 所有对象、金额、资格、动作、事实时点、章节顺序和编号均来自同一份冻结正文；
- content_hash 由该正文一次计算，后续只复制和校验，不重新分析、摘要、改写、拼接或从历史对象恢复。

正文必须完整覆盖当前 DWP：风险许可、三层监测、全市场机会发现候选、观察 ETF、机会 ETF、全部实际持仓、持仓 ETF 是否追加、现金、可释放资本、Trial/Confirm、实际部署金额、关键风险和后续条件。固定标题、章节顺序、一级/二级编号和名称（代码）格式按本协议后文执行；不得为了缩短报告而省略候选或持仓。

### 1.1.4 完成与通知合同

Formal Decision 的后置 completion ingress 固定使用现有 state-sync owner，但与 BDS 分离：BDS 使用 `request_type=BUSINESS_DECISION_SOURCE` 只保存当前业务事实和 FINAL_CONTENT；completion 使用 `request_type=STATE_SYNC_ONLY`、`source=CHATGPT_FORMAL_DECISION_COMPLETION`、`formal_fact_type=FORMAL_DECISION_COMPLETION`，并且只能引用同一 parent_request_id、decision_id、BDS path 和 content_hash。只有该 completion ingress 才允许形成 `events/reviews/formal_<occurrence_id>.json`。BDS 单独写入不得形成 Formal Decision event，不得直接进入通知投影。


用户回复和后置交接是连续但独立的两个动作：

1. READY 后完成业务判断并冻结唯一 FINAL_CONTENT；
2. 用户窗口立即输出同一份完整 FINAL_CONTENT；不得等待任何 BDS、completion、REPORT、notification 或 provider 状态；
3. 在同一执行回合内，将同一正文和同一请求身份写入现有 BUSINESS_DECISION_SOURCE 入口并回读；
4. 由同一请求直接提交既有 Formal Decision completion ingress，形成唯一 `events/reviews/formal_<occurrence_id>.json`，绑定当前 request/parent、decision、BDS、report、正文和 content_hash；
5. 由与【ETF系统复核】、【ETF交易复盘】相同的 decision-notification/notification_center/PushPlus 主链消费该 completion event；
6. 上述后置交接不得阻塞、改写、缩短或撤回 FINAL_CONTENT；任一后置对象缺失或不一致时，当前业务正文不撤回，按当前协议如实标记并 fail-closed；不扫描历史对象、不返回 ALREADY_MANAGED、不创建第二对象。

执行器约束：后置交接虽然是用户正文之后的投影，但不是可被静默丢弃的后台副作用。冻结 FINAL_CONTENT 后，当前 Formal Decision 执行回合在尝试并回读 BDS、completion 和当前 Formal review event 前不得被标记为完整结束；若执行环境在用户正文输出后无法继续执行，必须保留同一父请求并由运行态自愈/审计明确标记 `FORMAL_PARENT_HANDOFF_INCOMPLETE`，不得写成 HEALTHY、不得自动重放、不得改走 BDS 直通知或历史对象。该约束不改变“用户正文优先”：用户可见正文仍只是一份 FINAL_CONTENT，后置失败不得撤回、截短或改写正文。

BDS 不直接唤醒通知，也不直接调用 PushPlus。BDS 只负责业务事实入口和唯一正文冻结，并且必须携带完整的 presentation_binding（request_id、parent_request_id、decision_id、report_id、full_content、content_hash）；completion event 是通知主链的当前交接事件，REPORT 和 notification_center 只能从该精确事件透传同一份 FINAL_CONTENT，不得重新生成、摘要、改写或从历史对象恢复正文。PushPlus 未取得当前 notification 对应的明确 provider 成功结果时，只能记录 EXTERNAL_SEND_UNVERIFIED；历史 provider 成功不能证明当前请求发送成功。

### 1.1.5 状态和边界合同

机器消费状态只使用 WAIT、READY、TERMINAL；业务状态只使用：

- FORMAL_DECISION_CHAIN_PASS
- FORMAL_DECISION_CHAIN_FAIL
- GLOBAL_SYSTEM_HEALTH_DEGRADED
- MERGED_BUT_NOT_ACCEPTED

READY 不代表报告、通知或用户收到；workflow success 不代表业务链完成；全局 system consistency、System Review、交易复盘、post-close、行情、账户动态和 self-healing 失败不得改写当前 request-bound Formal Decision READY。正式决策只提供业务判断，不自动下单、不改变 MASTER、provider priority、monitor universe、交易权限或调度节点。


## 2. 唯一父请求

每次独立 Formal Decision 对应一个唯一父请求。重复催办、重试和“继续执行”必须恢复并消费同一父请求，不得创建等价请求、重复刷新或建立平行完成链。只有用户明确要求新的独立判断，或原请求已经终结后再次判断，才建立新请求。

## 3. 唯一三态输出合同

Formal Decision 对用户可见行为只有 WAIT、READY、TERMINAL 三种状态，不存在第四种解释状态。

### 交互回合与请求生命周期（执行语义澄清）

父请求的持续存在不等于ChatGPT当前交互回合可以无限运行。WAIT的零用户输出是**同一仍在执行的交互回合内**的行为约束，不是允许以空白assistant消息、空final或无正文完成态结束回合的许可。必须明确区分：机器请求仍未终结、模型当前回合仍在执行、用户已经收到业务回复，这三件事互不等价。

- 只要当前交互回合仍可继续执行，WAIT就继续消费同一父请求，不产生任何用户可见输出，不人为提前终止。
- 如果当前交互运行环境不能保证继续执行，不得以空白final伪装WAIT或宣称任务完成；父请求保持原身份和正式状态，恢复时优先读取同一父请求已发布的事实，禁止重新创建等价请求或重复刷新。恢复不能依赖模型记忆猜测身份。
- 现有交互平台若不能在没有用户新消息的情况下重新唤醒已结束回合，单靠本协议不能保证自动恢复；不得声称具有不存在的后台唤醒能力。用户再次催办时恢复同一父请求，若已READY直接输出完整报告。
- 不能把“用户端未收到报告”当作TERMINAL，也不能用聊天空白消息代替READY交付。真实交互验收应单独记录回合是否结束、用户是否收到正文和同一请求是否最终交付。

此澄清不新增第四种业务状态、不放松READY证据门、不新增workflow或第二套恢复链，也不授权在WAIT输出过程消息。

### WAIT

同一父请求仍处于 queued、building、in-flight，或当前只能读取旧、空、未绑定的派生上下文。

必须：

- 不产生任何用户可见输出；
- 继续消费同一父请求；
- 不创建第二请求；
- 不输出失败、等待、处理中或内部状态说明。

旧上下文、空上下文、连接器暂时空读、一次超时和 request_id 尚未匹配，只能解释为 WAIT；除非已有明确 terminal failure，不得提前终止。

### WAIT循环固化规则（强制）

WAIT不是一次判断，而是同一父请求上的连续消费循环。每一轮循环必须按以下顺序执行：

1. 回读同一父请求及其现有canonical链状态，重新读取formal_reply_gate，并核对request_id仍等于当前父请求。
2. 若状态仍为queued、building、in-flight、旧context、空context或未匹配，只能继续消费同一父请求；不得创建第二请求、重复刷新、复用旧事实或输出中间状态。
3. 若状态变为合法READY，立即退出WAIT循环并进入完整业务判断和首份用户报告；不得继续等待后置持久化、审计、通知或验收。
4. 若状态明确为TERMINAL_FAILURE且不存在合法继续路径，退出WAIT循环并仅输出一次真实终止说明。
5. 单次空读、单次超时、后台排队或派生状态暂未追上父请求，均不得直接跳出循环；只有明确终止条件才允许结束。

该循环只允许沿现有父请求、canonical workflow和既有状态投影继续消费，不新增workflow、producer、state、writer、completion或通知链。若当前交互回合已无法继续执行，按本节“交互回合与请求生命周期”处理：保持原父请求身份和状态，后续恢复时继续读取同一父请求，不得伪造WAIT完成或重新创建等价请求。

### READY

同一父请求的合法 request-bound facts、DWP、PIT、freshness、market phase 和业务判断前置条件已经满足。

必须：

- 立即完成完整业务判断；
- 第一条用户可见回复直接输出完整 Formal Decision 长报告；
- 不等待持久化、审计、通知、consistency、acceptance 或其他后置投影；
- 不重新生产、刷新、拉取或重建已经 request-bound 的正式行情／事实，不重复 Discovery，不重新审计已经通过的生产链；
- 允许为完成本次 DWP 中机器无法替代的业务判断，有限、按需取得尚未包含在 request-bound 包中的决策相关外部补充证据；这类补证不得覆盖 request-bound/canonical 正式事实、冒充当前可执行价格或证券身份、改变 provider 优先级、绕过 PIT/freshness、扩大交易权限或建立第二 producer/state/decision chain；
- 外部补证必须保留事实时点、来源和用途边界；已有 request-bound 证据足够时不得机械重复查询，补证失败、超时或空结果按现有规范合法降级，不得阻塞已经具备的 READY 回复资格。
- 外部工具的“已连接”、某一 endpoint 调用成功或能够解析证券身份，不等于其对目标对象、目标市场、目标时段或目标数据类型具有行情／研究权限；必须按本次实际 endpoint 返回、entitlement／套餐／quota、对象覆盖和数据时点分别判断证据资格，不得由一个成功 endpoint 外推同工具其他 endpoint 可用。

### TERMINAL

同一父请求存在明确 terminal failure，且不存在可以继续消费的合法链。

才允许：

- 输出一次终止说明；
- 说明真实原因和必要的人工下一步；
- 不将非终态读取结果解释为终止失败。


## 3.1 当前请求主动刷新事实的时点例外（强制）

当父请求明确设置 `force_refresh=true` 或 `require_post_request_snapshot=true` 时，本次请求创建后由既有正式链生成的 snapshot、Discovery、query_context、DWP 和 decision context，属于本次请求的合法事实，不得仅因其生成时间晚于 `requested_at_beijing` 而排除。

“不得使用触发时点之后产生的事实”仅适用于与本次父请求无关的后续运行、其他请求、未来节点事实或未绑定身份的事实；不适用于本请求明确要求生成的 post-request snapshot。执行顺序必须是：写入并回读当前父请求 → 等待同一请求的正式事实发布 → 读取同一请求的 gate → READY 后完成判断。等待本请求事实发布期间，只能继续消费同一请求，不能以“触发后生成”为理由终止、创建第二请求或复用旧事实。

本次请求的合法事实截止边界，应以该请求实际绑定并被 gate/DWP确认的正式事实为准，而不是机械以父请求创建时间截断。

## 4. Actor确定性执行边界

Formal Decision中，ChatGPT只负责机器无法确定性替代的业务判断；请求身份、状态解释、用户输出资格、生产输入结构和恢复语义属于机器合同，不得仅依赖自然语言指令或模型记忆。

### 4.1 状态解释与下一动作

现有 `scripts/formal_request_consumer.py` 是消费侧唯一三态解释器。既有 `scripts/build_e2e_status.py` 必须直接复用该解释器，并把结果投影到既有 `data/state/e2e_status.json` 的 `components.decision_context.formal_reply_gate`，作为GitHub外部Actor的首要机器交接面；不得在E2E层重写第二套状态算法。外部Actor必须先核对gate的 `request_id` 等于当前父请求，再直接消费其中的 `consumer_status`、`actor_action`、`user_output_allowed` 和READY必答清单，不得仅凭 `reply_freezable`、Dashboard、旧context或自然语言自行推导输出资格。解释器每一条返回路径都必须同时给出 `actor_action` 与 `user_output_allowed`；缺少任一字段即属于实现不完整，不得交给ChatGPT自行解释。两者共同构成Actor执行合同：

- `CONTINUE_SAME_REQUEST_SILENTLY`：继续消费同一父请求，且不得产生过程性用户输出。
- `COMPLETE_BUSINESS_DECISION_NOW`：立即完成本次业务判断，不等待后置持久化、审计、通知或验收。
- `TERMINATE_ON_EXPLICIT_FAILURE`：仅在明确TERMINAL条件下允许一次终止说明。

ChatGPT不得根据旧context、空读、单次超时、历史状态或聊天记忆覆盖机器给出的Actor动作，也不得发明第四种用户可见状态。


### 4.1.1 当前请求 READY 与全局健康状态的优先级（强制）

Formal Decision 的用户回复资格只由当前父请求的 request-bound `formal_reply_gate` 决定。必须严格区分当前请求业务回复门与全局健康、系统复核、收盘复盘及后置投影：

- 当前请求 `formal_reply_gate.status=READY`、`reply_freezable=true` 且 `request_id` 匹配时，必须立即进入业务判断并输出完整 FINAL_CONTENT；
- 顶层 `e2e_status=BLOCKED`、`GLOBAL_SYSTEM_HEALTH_DEGRADED` 或其他全局状态不得覆盖当前请求 READY；
- `close_review` 未完成、completion 未写入、REPORT 未形成、notification 未发送或 provider 结果未知，均属于后置状态，不得阻断当前 READY 业务回复；
- 顶层状态只能作为系统健康和后置闭环可见性，不得被外部 Actor 用来重新推导当前 Formal Decision 的用户输出资格；
- 当前请求未 READY 时，外部 Actor 不得把全局状态、旧 gate 或旧 DWP 解释为当前请求终止，也不得向用户展示内部中间状态；
- 当前请求 READY 后，第一条用户可见消息必须是完整业务判断，不得输出“暂时不能进入业务判断”、workflow、gate、DWP、close_review 或提交回执。

该优先级只固化既有三态合同，不新增状态、workflow、producer、checker、writer 或通知链。

### 4.2 READY业务完整性门

READY只表示业务判断可以立即开始，不表示ChatGPT可以跳过DWP中的部分业务问题直接生成报告。为避免“先回复、再靠用户复核补漏”，同一request-bound DWP的`problem_graph`同时是本次Actor业务判断的确定性完整性清单。

在第一条Formal Decision长报告形成前，ChatGPT必须先在当前交互内完成以下动作：

- 以消费器READY结果中的`required_problem_ids`（来源仅为同一DWP `problem_graph`）作为必答集合；不得用聊天记忆、固定对象清单或上一次报告代替；
- 为每个required problem形成业务答案；Discovery、Observation／Observation Review、全部实际持仓、持仓ETF追加、资本状态及DWP登记的其他问题均不得因报告篇幅被跳过；
- 业务答案集合必须与required problem集合完整覆盖；少任何一项都不得声称“完整Formal Decision已经形成”；
- 用户报告可以对“动作、资格、理由均相同”的对象合并表达，但必须枚举被合并对象并保留其角色边界；不得用“其他候选”“其余观察对象”等无法证明覆盖范围的概括替代必答对象；
- 该完整性检查只发生在Actor当前交互内，是业务回答形成的一部分，不写canonical状态、不新增checker/workflow/state，也不得等待后置BUSINESS_DECISION_SOURCE持久化。业务判断完成后仍按第6节先向用户交付报告，再异步提交既有BUSINESS_DECISION_SOURCE；
- 既有`project_decision_response`对`decision_response.answers`与DWP problem ids的fail-closed校验继续作为后置正式入口保护，但不得把该后置拒绝当成首份用户报告完整性的替代机制。

消费器在READY时必须返回`required_problem_ids`、`required_problem_count`和固定`business_answer_contract=DWP_EXACT_COVERAGE_BEFORE_PRESENTATION`，使Actor不需要自行重新推导必答集合。WAIT与TERMINAL不产生该READY清单。

### 4.2 请求恢复与重复输入

同一未终结父请求上的“继续”“重试”“再执行”等催办，只改变消费时机，不改变请求身份。恢复身份必须以正式父请求和机器状态为依据，不以聊天摘要、模型记忆或最近一次可见回复猜测。重复等价输入不得创建第二父请求、第二刷新或第二决策链。

### 4.3 业务回答与生产结构分离

ChatGPT不得自由手写Formal Decision内部生产结构。READY后只回答request-bound Decision Work Package要求的业务问题；父请求绑定、snapshot绑定、字段投影和正式结构由既有BUSINESS_DECISION_SOURCE builder/validator/owner负责。机器能够确定的字段不得要求ChatGPT凭记忆补齐或猜测。

任何Actor输入在进入正式owner前若可进行确定性schema或identity校验，应先校验再写入；校验失败必须fail-closed，不得依赖下游异常来发现结构错误。

### 4.4 证券身份、名称与代码

证券身份遵循数据规范的既有身份合同。用于动作、持仓、机会和资本比较的对象必须来自request-bound账户事实、Discovery/观察/机会事实或已核验provider身份；不得仅凭自然语言名称、代码前缀、历史聊天或模型知识猜测证券身份、交易所、asset type或经济暴露。

机器关联优先使用已核验代码和市场身份；名称属于展示字段，不是身份主键。用户可见报告优先显示“已核验中文名称（代码）”；若名称尚未被合法事实确认，只显示代码或明确身份待确认，不得由ChatGPT补造名称。名称与代码、交易所或asset type发生冲突且会改变动作或资本比较时，必须在READY前解决或按数据规范合法降级。

同名、简称、名称变更、跨市场同代码、指数本体与ETF代理不得依靠名称相似度自动合并。指数、ETF、个股及代理关系必须保持真实对象身份。

用户只给出证券名称、简称或自然语言描述时，不得把“请用户补代码”作为默认前置条件；应按数据规范使用既有身份解析/核验路径自主解析。能够唯一核验时继续执行；无法唯一核验且该身份会改变正式动作时才形成真实身份缺口。反过来，用户只给代码时也不得由模型知识补造名称。

### 4.5 事实、推断与故障归因

ChatGPT必须区分已确认事实、基于事实的业务推断和尚未证明的故障假设。单个中间状态、异常或一次未收敛结果不能自动升级为生产代码根因；应先排除正常非终态、输入错误、权限或工具问题以及既有恢复机制。该要求不阻塞已经READY的业务回复。


## 4.1 节点证据获取与外部工具路由合同

Formal Decision必须完成与当前市场节点相匹配的证据核验，但不要求每次机械调用全部外部工具。强制对象是“节点证据核验完成”，不是“插件调用次数”。

每次正式判断必须先识别当前节点、市场阶段和会改变本次资本判断的证据需求，再选择适用工具：

- GitHub：正式规则、request-bound账户/行情/Discovery/DWP、状态和验收事实的唯一权威入口；
- Longbridge或其他适用市场工具：市场开闭状态、关键持仓/候选对象的辅助行情和市场状态核验；
- Bigdata：宏观、行业、公司事件或研究事实可能改变机会、持仓或资本迁移判断时调用；
- Exa或其他适用检索工具：需要核验公告、政策、突发事件或外部研究时调用；
- 云浏览器：仅用于登录态页面、GitHub Actions/PR/CI、券商页面或其他必须页面交互的核验，不作为常规行情的第二来源。

节点路由至少遵循：盘前核验海外隔夜与市场阶段；盘中核验关键对象和当前市场状态；午间核验上午结构及跨市场反馈；收盘核验收盘状态、跨市场收盘反馈和下一交易日可能影响。若本节点没有可能改变判断的外部事件，Bigdata、Exa或云浏览器可以不调用，但必须以现有正式事实或适用工具完成必要核验。

外部工具结果只能作为辅助证据，必须保留实际时点、对象身份、市场阶段和数据性质；不得覆盖request-bound/canonical账户、行情、PIT、freshness、证券身份、交易权限或MASTER结论，不得把辅助行情冒充当前可执行价格。工具调用失败、超时、空结果或不适用时，按数据规范作合法降级，不得用猜测补齐，也不得因此自动宣称Formal Decision失败。

工具调用只有在结果实际进入三层监测、候选比较、持仓判断、资本竞争或风险说明时，才算有效证据消费。已有request-bound事实足够时不得机械重复查询；也不得为了形式上调用插件而增加延迟和噪音。READY后适用的外部补充查询不得阻塞已经取得的业务回复资格，且不得重新刷新或重建同一请求的正式事实链。

Formal Decision验收应分别记录：节点证据需求是否满足；适用工具是否成功调用或存在合法降级；调用结果是否被业务判断实际消费。未调用某个插件本身不构成失败；未完成本节点必要证据核验，或调用结果完全未进入判断，才构成证据消费不完整。

### 4.1.1 外部辅助证据编排与安全并行

外部补证按业务问题而不是按工具名单机械执行。Actor可读取 `config/research/external_auxiliary_capabilities.json` 作为只读能力提示，先识别 `EVIDENCE_NEED_PLANNING`，再做 `CAPABILITY_MATCH`，随后对相互独立的只读辅助证据执行 `AUXILIARY_EVIDENCE_FETCH`，最后在同一交互内完成 `EVIDENCE_SYNTHESIS`。这些名称只描述Actor执行阶段，不是持久化状态，不新增workflow、producer、checker、writer或第二决策链。

允许并行的是不同且相互独立的辅助证据域，例如市场行为、公司研究、ETF结构、跨市场反馈和事件核验。不得并行或重复生产父请求、request-bound snapshot、DWP、Discovery、账户、正式行情、PIT/freshness或BUSINESS_DECISION_SOURCE；这些仍沿既有单一正式链。

并行补证必须在业务判断前汇合：实际采用的结果统一核验对象身份、事实时点、market phase、entitlement/coverage和数据性质，再进入三层监测、候选比较、持仓判断、资本竞争或风险说明。某一辅助分支超时、空结果、quota或entitlement受限，只降低该证据域强度；已有合法充分证据时不得等待可选分支而延迟READY回复。

优先并行不同证据域，而不是对同一事实机械多源查询。只有身份冲突、重大事件、数据异常或结论对单一来源高度敏感时才追加第二来源核验。同一证据域涉及多个对象时，若工具真实支持batch，应优先batch；否则可在Actor层并行只读获取，但每个对象的业务影响仍须可区分。

能力登记中的 `parallel_safe`、`independent_fetch_group`、`stop_when_sufficient` 等字段只是执行提示，不构成provider priority、正式行情fallback、交易权限或证据事实。registry不得持久化每次调用结果；真正影响判断的证据仍必须通过既有 `answers[].evidence_decision_impact → decision_evidence_consumption` 合同消费。

## 5. READY前的业务门禁

READY 前必须完成或按正式规范合法降级：

- request-bound facts、PIT、freshness 和 market phase；
- 海外与跨市场、A股内部反馈、ETF与资本机会三层监测；
- 全市场机会发现、观察 ETF、机会 ETF；
- 全部实际持仓逐一判断；
- 现金、可释放资本和完整资本竞争；
- 符合 MASTER 的风险、Trial、Confirm、金额、集中度和卖出判断。

本协议不复制上述规则，只要求按所属正式文件读取和执行。用户最终决定并人工下单，ChatGPT不得自动提交证券订单。

## 6. 回复与后置生产分离

合法 READY 后，Formal Decision 执行顺序为：

`request-bound facts → 业务判断 → 冻结 FINAL_CONTENT → 用户窗口输出同一 FINAL_CONTENT → BDS写入并回读 → Formal Decision completion ingress → REPORT/notification_center → PushPlus`

用户窗口输出是READY后的首个可见动作；BDS写入、回读、completion、REPORT、notification和PushPlus均为后置投影。BDS不是通知唤醒源，必须由既有Formal Decision completion ingress形成当前Decision completion event后进入通知主链。PushPlus/provider成功不作为用户回复门禁。后置失败不得阻塞、撤销或改写完整用户正文，但必须保留当前失败或未确认事实。

ETF交易复盘和ETF系统复核仍遵循各自已验证的Scheduled Review completion/report路径，不因本节Formal Decision减法而改变。

### 6.1 READY回复与后置正式交接的不可混淆边界

ChatGPT完成业务判断后只生成一次冻结的FINAL_CONTENT。用户窗口先输出同一份完整FINAL_CONTENT；随后由既有Actor handoff提交一次BUSINESS_DECISION_SOURCE envelope并回读，确定性核对parent_request_id、request_type、decision_response.answers覆盖同一DWP必答集合、presentation_content逐字等于冻结的FINAL_CONTENT，以及content_hash一致。

用户窗口输出是 READY 后的首个可见动作。不得等待 BDS 写入/回读、completion、REPORT、notification 或 PushPlus；通知尝试不得先于用户窗口输出。用户正文必须完整包含冻结的 FINAL_CONTENT，不能只返回 HANDOFF 状态、通知状态、内部校验结果或任何占位说明。

用户正文输出后，Actor才将同一FINAL_CONTENT写入BDS并回读，随后提交既有Formal Decision completion ingress，由既有REPORT/notification_center/PushPlus主链异步投影。BDS的presentation_content必须逐字复用FINAL_CONTENT；后置交接或provider失败不得阻塞、撤回、截断或改写用户正文，也不得创建第二正文或第二通知链。

BDS提交、回读或通知尝试失败时，必须保留同一父请求和冻结的FINAL_CONTENT；不得创建第二请求、第二正文、第二BDS、历史回退或旁路通知。通知失败不改变用户业务判断，但不得宣称当前notification或provider成功。

Formal Decision通知验收必须核对同一父请求的 BDS、completion event、events/reviews/formal_<occurrence_id>.json、当前 notification、PushPlus provider result 和用户实际收到状态。通知正文可以在共享 notification_center 内部表现为 `REPORT_DELIVERY_REQUEST`，但该对象只能由当前 completion event 投影形成，不得作为独立生产入口、唤醒源或历史回退。Scheduled Review仍按其自身合同核对completion、REPORT和REPORT_DELIVERY_REQUEST。

## 7. 用户可见边界

## 7.1 用户可见表达最小规范

正式报告只使用自然业务语言，不向用户展示内部机器分类、控制面术语、字段名、状态枚举或实现阶段名称。

用户可见业务身份统一为：

- **持仓ETF**：账户事实实际持有的ETF；
- **观察ETF**：已取得正式观察身份的ETF；
- **机会ETF**：完成本节点正式机会判断后取得机会身份的ETF；
- **全市场机会发现**：全市场扫描和候选发现过程，其结果只是本节点候选评估输入。

必须明确：全市场机会发现结果不等于观察ETF，不等于机会ETF，不等于Trial／Confirm资格，也不等于交易资格；候选被评估不代表获得任何正式身份或交易许可。

以下术语不得出现在用户可见正文中：

- “Discovery对象”、“Discovery候选”、“资本比较包”、“正式包”、“request-bound facts”、“provider”、“质量PASS”；
- “节点级机器分类”、“对象级机器分类”、“formal package”、“canonical”、“writer”、“BDS”、“workflow”、“state”、“producer”、“checker”、“identity”。

统一替换为自然业务语言：

- Discovery对象／Discovery候选 → “全市场机会发现结果”或“全市场机会发现中的候选输入”；
- 资本比较包 → “现金、持仓ETF、观察ETF、账户个股和全市场机会发现结果的资本用途比较”；
- 正式包 → “本次正式判断所用数据”；
- request-bound facts → “本次正式判断绑定的同一时点事实”；
- provider为…… → “行情来源为……”；
- 质量PASS → “数据质量合格”；
- “4项持仓ETF、4项其他持仓ETF” → “8项实际持仓ETF”；
- “A股17个对象覆盖可用” → “A股主要指数和ETF行情覆盖完整”。

“关键变化”只能写会改变风险排序、候选排序、持仓判断、资本用途或交易资格的业务事实。快照更新、进入连续竞价、时段切换、数据覆盖变化，以及本次正式判断绑定事实形成或刷新本身，不得机械列为“关键变化”；如确有必要说明，只能放在“数据时点与必要依据”中。

如相较上一节点风险权重发生变化但交易结论没有变化，应明确说明：“相较上一节点，某方向风险权重上升／下降，但尚未同时满足改变持仓、资本用途或交易资格所需的条件，因此本节点交易结论暂不改变。”

正式回复只表达业务结论、数据时点、三层监测、持仓动作、资本用途、风险、后续关注点、置信度和最可能出错的地方。

不得向用户展示 request_id、文件路径、commit、workflow、run ID、provider 日志、canonical、BDS、writer、Decision Fact、consistency、acceptance、内部枚举、重试记录或“自动接管”等控制面术语。

## 7.1.1 固定用户可见报告版式

正式 Formal Decision 长报告必须沿用以下固定版式，不因手动入口、定时入口、盘前、盘中、午间或盘后而另行创造格式：

1. 首行使用“【ETF正式决策】YYYY-MM-DD HH:MM 正式判断”，其中时间为本次正式判断的北京时间；首行后空一行。
2. 一级业务章节统一使用全角方括号标题，并按以下顺序组织：
   - 【业务结论】；
   - 【数据时点与必要依据】；
   - 【三层监测】；
   - 【持仓动作】；
   - 【资本用途】；
   - 【五项关键变化】；
   - 【关键风险】；
   - 【后续安排】；
   - 【置信度】；
   - 【最可能出错的地方】。
3. 每个章节之间空一行；章节内部使用连续编号，只有确需逐项展开的持仓、观察ETF和全市场机会发现候选输入才逐项列示。不得使用Markdown一级或二级标题替代固定章节标题。
4. 【业务结论】必须先给出风险判断、机会/资格判断、实际部署金额、现金用途、持仓动作和可释放资本；不得先讲系统运行过程。
5. 【数据时点与必要依据】必须明确行情事实时点、市场阶段、账户事实时点、现金、实际持仓总数，以及收盘参考或休市参考不等于当前可成交价格的边界。
6. 【三层监测】必须分别覆盖外部与跨市场、A股内部反馈、ETF与资本机会；某一层合法降级时说明其业务影响，不得以省略该层代替说明。
7. 【持仓动作】必须覆盖账户事实中的全部实际持仓，并分别说明持有、追加、风险型卖出、可释放资本和重新判断条件。账户实际持仓总数与持仓ETF数量必须区分，例如“8项实际持仓，其中6项持仓ETF和2项账户个股”。
8. 【资本用途】必须同时覆盖现金、持仓ETF追加、观察ETF、全市场机会发现中的全部候选输入、固定Trial金额、资金缺口、可释放资本和实际部署金额；候选输入必须逐项列明已核验名称和代码，名称未被正式事实确认时只显示代码。
9. 【五项关键变化】固定列出最多五项真正改变风险排序、候选排序、持仓判断、资本用途或交易资格的业务事实。快照刷新、进入连续竞价、时段切换、数据覆盖变化和事实形成/刷新本身不得机械列入；如需说明，只放入【数据时点与必要依据】。
10. 正式报告不得加入通知发送结果、持久化结果、运行编号、验收结果、提交结果、后置任务状态或其他内部交接说明。此类信息不属于用户可见业务正文。

当本节点没有新增机会、资格或动作时，仍必须保留上述章节和逐项覆盖；可以明确写“本节点无变化”，不得以缩短报告替代完整判断。固定版式只约束用户可见呈现，不改变交易规则、资本竞争范围、资格阈值、通知链或任何内部机器结构。

### 7.1.2 与【ETF正式决策】定时任务的固定格式一致性

本协议与【ETF正式决策】定时任务共用同一用户显示合同；定时任务指令中的下列硬要求在手动入口和自动入口均同等适用：

- 冻结的唯一FINAL_CONTENT以正文首行开始，且正文首行仅出现一次，格式为“【ETF正式决策】YYYY-MM-DD HH:MM 正式判断”；不得把任务计划时间机械写入报告时间。通知平台外层标题“【ETF正式决策】”属于正文外展示，不得复制到FINAL_CONTENT内部或被误解为第二个正文标题。
- 上述唯一正文首行后空一行，再开始第一个正文区块；不得再增加第二标题、顶部重复摘要、通知摘要或第二份正文。
- 章节标题必须单独一行，标题后空一行；章节顺序固定，不得用其他标题体系替代。
- 证券对象优先使用“名称（六位代码）”格式；同一清单内同一代码只出现一次，主候选已单列时不得再次列入其他候选或观察清单。
- 表格、加粗和其他富文本只能增强可读性，不能承载唯一业务信息；纯文本通知必须保持完整可读。
- Formal Decision 通过 PushPlus/微信发送时必须使用纯文本模板（不得使用 Markdown 模板）；PushPlus/微信纯文本中的多项内容使用独立数字序号，不使用圆点、小横线或依赖Markdown表格分段；正式正文不得因通知渠道而产生摘要、改写或第二正文。
- 时间和状态分开表达：任务时间、事实时间、报告生成、通知发送和用户收到不得混写；READY或系统可用不等于交易许可。
- 格式、名称、去重或展示无法进一步规范时，保留已确认事实并继续完成，不得因此等待、失败或阻塞正式判断。


## 7.2 业务判断与资本用途表达合同

正式回复必须区分以下四层含义，不得互相替代：

- **全市场机会发现中的候选输入**：进入本节点比较范围、但尚未取得观察ETF或机会ETF身份的结果；
- **机会 ETF**：经本节点证据比较后具备机会角色的对象；
- **Trial／Confirm 资格**：依照 MASTER 已达到相应交易阶段，但不等于已经执行；
- **实际部署**：本节点最终允许并决定投入的金额，必须单独写明。

报告称某对象为“机会 ETF”时，必须同时说明其是否取得 Trial／Confirm 资格及实际部署金额；未取得资格的对象只能表述为候选或继续比较对象，不得将近期强势、历史收益或入选发现列表直接表述为买入许可。

当现金不足以完成固定 Trial，必须明确列出：固定 Trial 金额、当前现金、资金缺口、最低效率持仓或资本迁移排序、拟释放资本及最终释放金额。最终释放为零时，也必须说明“没有任何持仓达到独立卖出门槛”，不得只写“现金不足”或“保留现金”。

若本节点没有任何对象达到新增部署门槛，统一表述为“当前不存在足以支持新增部署的合法机会；现金继续保留，实际新增部署为0元”，不得把“现金”本身表述为机会或资本用途。下一单位资本的比较对象应写成当前最值得继续竞争的机会候选，并明确其尚未取得的交易资格。
持仓依据、资产角色和替代功能必须来自本次正式事实、MASTER或已登记的机器角色；事实未支持的“打新底仓”“防御功能”等解释不得由ChatGPT自行补造。若只能作业务推断，必须明确标注为推断，不得写成已确认事实。

用户可见报告应区分正式收盘行情、收盘后参考行情和下一交易日可执行价格；收盘后价格不得被表述为当前可成交价格。


## 7.3 首份READY报告完整消费合同

READY后的首条用户可见业务回复必须直接消费同一请求的完整DWP，不得把逐项问题压缩为只有结论的概括性说明。完整报告至少逐项覆盖：

- 全市场机会发现中的全部候选输入：其评估范围、是否取得观察ETF或机会ETF身份、Trial／Confirm资格和实际部署金额；
- 全部观察ETF：是否保留观察身份、是否成为本节点机会，以及未升级为机会或未取得部署资格的原因；
- 全部实际持仓：持有依据、风险型卖出判断、资本效率释放判断、持仓ETF追加判断、最终动作和改变动作的条件；
- 现金、持仓追加、可释放资本和新增机会之间的完整比较；
- 没有新增、没有资格、没有释放资本或没有变化的对象，也必须明确说明，不得静默省略。

本合同约束首份用户可见报告的事实消费和展示完整性，不改变READY资格，不新增业务状态，不阻塞已经具备合法业务回复资格的判断，不撤销已取得的回复资格，也不建立第二条决策链。机器DWP、正式事实和交易规则仍由既有owner确定；ChatGPT只负责完成需要业务判断的回答并按本合同完整呈现。

## 7.1.3 正式完成事件写入路径与逐项候选覆盖（强制）

1. ChatGPT 完成业务判断后先冻结唯一 FINAL_CONTENT，并立即向用户输出同一份完整正文；用户回复不得等待 BDS、completion、REPORT、notification 或 provider。

2. 用户正文输出后，Actor 才能提交唯一 BUSINESS_DECISION_SOURCE envelope，写入 current main 的：

   requests/live_snapshot/<parent_request_id>__business_decision_source.json

   或现有兼容命名 requests/live_snapshot/<parent_request_id>__bds*.json。BDS 是当前业务判断和冻结正文的业务事实源，不是通知 workflow 的唯一唤醒机制。

3. BDS 写入并回读成功后，由既有 Formal Decision completion owner 提交唯一 completion ingress，形成并持久化：

   events/reviews/formal_<occurrence_id>.json

   completion event 必须绑定同一 request_id、parent_request_id、decision_id、report_id、bds_path、bds_commit、content_hash 和完整 presentation_binding.full_content。只有该 completion event 才能进入通知主链。

4. 主动通知必须由既有 notification workflow 从本次 completion event 的精确路径唤醒并消费；不得依赖 BDS 写入后的 market-snapshot 递归唤醒，不得扫描历史目录、按文件名排序或按最新 commit 猜测当前正文。通知 workflow success 不等于 notification_id、provider result 或用户收到。

5. 任一后置对象缺失或不一致时，当前业务正文不撤回；后置交接按当前协议如实标记并 fail-closed，不扫描历史对象、不返回 ALREADY_MANAGED、不创建第二 BDS、第二 completion event、第二份正文或旁路发送路径。

3. 用户可见报告中的全市场机会发现候选必须逐项覆盖。每项至少说明：名称和代码、是否进入本节点观察评估、是否取得观察ETF或机会ETF身份、Trial/Confirm资格、实际部署金额，以及未进入下一层的业务原因。候选可以使用紧凑的编号清单，不得用一句共同结论替代全部候选的逐项判断。全市场机会发现结果只是候选评估输入，不因被列出而取得任何正式身份或交易资格。

4. “五项关键变化”只保留能够改变风险许可、候选排序、持仓判断、资本用途或交易资格的业务事实。快照更新、进入连续竞价、时段切换、事实刷新、覆盖变化或本次事实包形成本身，不得机械列入；如仅改变事实时点而未改变业务结论，应放入“数据时点与必要依据”，不得列为关键变化。

## 7.1.4 用户可见编号与定时完成入口（强制）

1. 用户可见正文的一级清单统一使用阿拉伯数字加英文句点：`1.`、`2.`、`3.`；同一一级清单内不得改用圆点、小横线或其他混合标记。

2. 一级清单内部的二级逐项清单统一使用全角右括号：`1）`、`2）`、`3）`。二级清单不得继续使用与一级相同的`1.`格式；同一清单内必须保持层级和编号样式一致。该规则适用于资本用途、全市场机会发现候选、持仓逐项判断、观察ETF逐项判断及通知纯文本正文。

3. 自动节点与手动节点共用同一完成入口。定时任务完成正式业务判断后，必须将同一份冻结完整正文和同一父请求绑定的 BUSINESS_DECISION_SOURCE 写入`requests/live_snapshot/<parent_request_id>__business_decision_source.json`或现有兼容命名；仅在聊天窗口或任务运行结果中生成报告，不构成正式完成，也不触发主动通知。

4. 定时任务不得使用`requests/state_sync/`、`requests/report_delivery/`、`requests/report_handoff/`或其他路径替代正式完成入口。写入后必须回读同一路径，并分别核对业务判断完成、完成事件写入、通知工作流触发、PushPlus发送和用户收到状态；未写入正确入口时，不得声称主动通知已发送。


## 7.2.1 持仓资本竞争与继续持有证明责任（2026-10-15）

正式决策对当前实际持仓采用“释放优先审查”：

1. 每项持仓首先进入 `REDUCE_OR_EXIT_REVIEW`，作为可释放资本参加现金、其他持仓、观察ETF和Discovery候选的资本竞争；不得把继续持有作为默认基线。该审查状态不是自动卖出指令，而是正式决策的默认起点。
2. 最终动作可以是HOLD、REDUCE或EXIT，但HOLD必须由本次请求的正面证据证明：原持有逻辑仍成立、长期结果与占用资本审查已完成，并且继续持有相对释放为现金或其他合法用途具有明确理由。
3. “没有独立退出证据”“未达到独立卖出门槛”或类似否定性表述，不能单独构成HOLD理由。每项持仓还必须完成长期结果、资本占用和策略重置审查；若选择REDUCE或EXIT，必须记录正向减持/退出证据。证据不足时，默认停留在释放审查，不得自动将不确定性投影为继续持有。
4. 该规则只改变Formal Decision的审查顺序和证明责任，不构成自动卖出、自动清仓或证券订单授权；最终交易仍由用户确认并人工下单。
5. BDS、completion、通知和系统复核/交易复盘链条不得据此新增旁路或改变既有投递合同。
6. 买入与卖出采用对称证明责任：持仓的 REDUCE/EXIT 必须有当前风险、假设失效或资本迁移证据；候选的 Trial/Confirm 或资本迁移必须有当前机会证据、相对现金/持仓的胜出理由和下一节点执行门槛。单日强势、历史收益、相对排名或“存在候选”不能单独构成买入依据。 机会候选默认进入 `TRIAL_CONFIRM_REVIEW`；若最终不买，必须完成不买审查并说明不满足 Trial/Confirm 的具体原因，不能只写“当前不具备机会”或“等待观察”。

## 8. 验收边界

Implementation Acceptance 只能证明仓库实现、测试、PR、CI 和必要的 latest-main 验收符合本协议；不能单独证明真实 ChatGPT 交互路径已经遵守协议。

Real Target-Path Acceptance 必须验证：

- WAIT期间用户端保持零输出，机器Actor动作明确为继续同一请求；
- 旧context、空context、连接器暂时空读、单次超时、派生状态暂未追上父请求时仍保持WAIT，不提前失败；
- 重复“继续／重试”恢复同一父请求，不创建第二父请求或重复等价刷新；
- 同一父请求随后达到 READY；
- 第一条用户可见消息直接是完整业务报告；
- READY 后不等待后置生产闭环，后置失败也不撤销已经取得的业务回复资格；
- 明确terminal failure才允许终止说明；
- 用户报告中的证券名称（代码）来自本次request-bound或已核验身份事实，不以模型知识补造名称、交易所、asset type或代理关系；
- Actor提交的业务回答只覆盖DWP要求的问题，机器字段、身份绑定和正式结构由既有builder/validator完成。

对于曾由真实交互暴露的Actor失效模式，验收必须至少包含原失败场景、一个相邻非终态场景和一个此前正常READY场景。Implementation Acceptance与Real Target-Path Acceptance分别记录；前者不得替代后者。

在真实目标路径验收通过前，不得仅凭 helper、单元测试或 CI PASS 宣称本故障域已经闭环。


## Formal Decision业务验收与生产健康边界

Formal Decision业务验收只验证当前代码所定义的业务合同：三种入口身份能够绑定当前request_id，并在同一业务链中校验当前report_id、content_hash和notification_id的一致性；同时验证历史completion、REPORT和notification不得污染当前请求，BDS不得绕过completion直接触发通知。

Formal Decision业务验收不得读取或要求当天生产运行态，包括close_review_closure、CURRENT、账户事实、行情快照、post-close review、动态state文件或全局system-consistency结果。上述内容属于独立运行健康域，不是业务PR的输入，也不能阻断Formal Decision业务PR。

业务合同测试通过只表示代码业务链合同通过，不等于三入口真实生产请求已经产生，也不等于PushPlus用户端已送达。真实运行证据和外部发送证据必须单独标注；缺少当前request级provider回执时使用EXTERNAL_SEND_UNVERIFIED。

## 11. 今日故障经验的永久边界

本节把已验证的工程经验固化为业务协议边界，不把某次 PR、workflow run、SHA、时间戳或动态状态文件变成新的业务条件。

1. **正式决策与完整日复盘是两个独立业务链。** 复盘报告是否生成、落盘或通知成功，不得决定 Formal Decision 是否可以回复、完成或通知；Formal Decision 也不得代替完整日复盘。两者各自使用自己的 request、事实、报告和通知事件。
2. **READY 的首份报告不等待后置动作。** READY 表示当前请求已具备业务判断条件。Actor 必须用同一份冻结正文和同一份结构化答案完成当前交互回复；后置 BDS、completion、REPORT 和 notification 只能继续交接，不能反向阻塞、重写或要求重新生成正文。
3. **通知只有一条业务来源。** Formal Decision 的当前 BDS 是业务事实源，当前 Decision completion event 是通知唤醒和交接入口；completion、REPORT 和 notification 必须确定性绑定同一 request、decision、bds_commit 和 content_hash。snapshot、复盘、旧事件、旧报告或历史 PushPlus 结果都不能旁路触发或证明当前通知。Scheduled Review 继续按其自身 completion/REPORT 合同运行。
4. **运行健康与业务合并分离。** post-close、行情、账户、动态 state、self-healing 和 system-consistency 属于运行健康域；它们失败时报告 GLOBAL_SYSTEM_HEALTH_DEGRADED，不得把普通业务 PR 或已具备 READY 条件的 Formal Decision 改写为业务失败。
5. **CI 只证明代码合同。** PR CI 只验证变更代码、受影响模块和唯一 Formal Decision business contract。无 run 是 NOT_STARTED，不是成功；skipped、旧 run、旧 artifact 或其他 SHA 的结果不能证明当前 PR。SHA 仅是审计元数据，不是业务身份、完成条件或通知条件。
6. **失败必须按当前身份 fail-closed。** 当前 request、decision、report、content_hash 或 completion 缺失/不一致时，拒绝当前交接；不得扫描历史事件回退，不得返回 ALREADY_MANAGED 掩盖当前缺失，也不得创建第二请求或第二通知。
7. **外部发送状态分层。** 报告生成、notification request、provider 调用成功和用户实际收到是四个不同事实。只有当前 notification_id 的 provider 结果才可证明当前发送；无法取得时只能标记 EXTERNAL_SEND_UNVERIFIED。
8. **大文件读取不等于生产状态缺失。** 连接器空读或截断必须区分 READ_OK、VERIFIED_EMPTY 和 UNVERIFIED_READ，先按同一权威来源复核；不得把读取失败直接投影为 query_context 为空、Formal Decision 失败或需要新请求。

本节不新增 workflow、producer、checker、state、writer、acceptance 层或交易权限；只约束既有业务链的身份、时序、归因和 fail-closed 行为。


## 11.1 Formal Decision 唯一正文与主动通知绑定（2026-10-14）

Formal Decision 达到 READY 后，Actor 只冻结一份 `FINAL_CONTENT`。用户回复、completion 事件和主动通知必须引用同一份正文，不得重新分析、摘要、改写、拼接或从历史 REPORT／notification 恢复。

completion 的 `presentation_binding` 必须同时保存并校验：`request_id`、`parent_request_id`、`decision_id`、`report_id`、`full_content`、`content_hash`。其中 `full_content` 必须逐字等于 `FINAL_CONTENT`，`content_hash` 必须等于其实际 SHA-256。当前请求任一身份或哈希缺失、不一致时，必须 fail-closed，不得返回 `ALREADY_MANAGED`。

PushPlus 只能由现有 `notification_center` 从当前请求冻结的 `FINAL_CONTENT` 原文投影通知正文。Formal Decision 必须像【ETF系统复核】和【ETF交易复盘】一样，先形成当前精确 completion event，再进入共享 REPORT/notification_center/PushPlus 主链；BDS 只保存业务事实入口和同一份冻结正文，不承担通知唤醒。completion/REPORT 不重新生成正文，只透传同一份 `presentation_binding.full_content`。旧 completion、旧 REPORT、旧 notification 和历史 provider 结果均不得回退或补齐当前正文。`REPORT_DELIVERY_REQUEST` 如被现有兼容链使用，只能由当前冻结正文确定性投影，不是 Actor 需要新建的前置请求。

通知失败不撤回用户已看到的 `FINAL_CONTENT`；没有当前 `notification_id` 对应的 provider 成功结果时，状态只能记录为 `EXTERNAL_SEND_UNVERIFIED`。

## 11.3 正式正文二级编号与纯文本排版（2026-10-14）

1. 用户可见的一级编号统一使用 `1.`、`2.`、`3.`；二级编号统一使用 `1）`、`2）`、`3）`，不得在同一清单中混用圆点、小横线或不同编号样式。
2. 每个二级项目必须独立成段，项目之间使用两个真实换行符；不得把多个二级项目连续放在同一段，也不得仅用空格或单个普通换行分隔。
3. 二级项目采用悬挂缩进：编号与首行正文同属一个段落，续行必须与正文首行左边界对齐，不得回到页面最左侧；实现不得依赖手工空格或全角空格模拟缩进。
4. 该版式必须在 `FINAL_CONTENT` 冻结前形成；BDS、PushPlus、微信和用户窗口均逐字转发同一正文，不在通知链再次排版、摘要或改写。
5. 本节只约束用户可见排版，不改变交易规则、机会资格、资金金额、通知资格或任何生产链路。

## 11.2 冻结正文与主动通知的直接投影边界（2026-10-14）

1. 用户可见正文必须先按本协议固定版式生成并冻结为唯一 `FINAL_CONTENT`：首行、章节顺序、章节空行、一级编号 `1.`、二级编号 `1）` 必须各自独立起行并独立成段（不得在同一段内以内联 `（1）…（2）` 连接；续行由纯文本客户端自然换行，不依赖富文本表格）、证券“名称（六位代码）”格式、同一清单去重和纯文本可读性均属于正文合同。PushPlus、微信及其他渠道只能原文转发，不得增加通知标题、摘要、状态、内部字段或第二份正文。
2. 主动通知是 `FINAL_CONTENT` 的投影，不是 BDS 业务答案的再次校验，也不是 REPORT 重建流程。现有 `notification_center` 应从当前 Decision completion event 的 `presentation_binding.full_content` 投影通知；completion event 必须证明其绑定的 BDS path、BDS commit、当前 request/parent、decision、正文和实际 `content_hash`。BDS 仅作为业务事实源和 completion 绑定对象，不得被旧文件扫描或历史 REPORT 替代。Scheduled Task来源另外校验 `task_id`、`task_run_id`、`occurrence_id`，手动对话来源不得强制要求这三个定时任务专用字段。不得要求 `decision_response` 的业务字段（例如 `RISK_PERMISSION.final_action`）才能转发正文。
3. BDS 结构化校验、Decision Fact、completion 和 REPORT 仍各自按既有合同运行，但任何后置结构化投影失败都不得撤回已经生成的 `FINAL_CONTENT`，也不得阻断同一当前请求的原文主动通知。通知不得从 BDS answers、旧 REPORT、旧 completion 或历史 notification 重建正文。
4. 当前 Formal Decision completion event 的通知投影只能由现有 `decision-notification.yml` 唤醒并调用既有 `notification_center`；不得新增 producer、sender、workflow、state 或旁路通知链。触发事件必须精确绑定本次 completion event 及其当前 BDS，历史目录扫描、标题匹配和旧请求回退一律禁止。
5. 当前正文、身份或哈希缺失/不一致时，通知必须 fail-closed；不得返回 `ALREADY_MANAGED`，不得补发旧正文。用户已看到正文但当前 PushPlus 无当前 notification/provider 成功证据时，用户业务回复仍保留，外部状态只能记为 `EXTERNAL_SEND_UNVERIFIED`。



## 1.1.6 三入口统一正文质量门与交付幂等（V2）

三个入口均必须进入同一结构化业务判断和唯一正式正文渲染器；入口身份、等待预算、SKIP语义和task字段可以不同，但不得产生不同的正文生成、校验或通知逻辑。

正式正文必须通过统一质量门后才能写入 BUSINESS_DECISION_SOURCE：

- presentation_content、decision_response.presentation_content、presentation_binding.full_content（如存在）必须逐字一致；
- 正文必须完整覆盖当前DWP和固定章节；字符长度仅作质量观测，不得作为用户回复、BDS、completion或通知的硬门槛；
- 当前DWP中的持仓、持仓ETF、观察ETF、全市场候选、机会ETF、现金、可释放资本、Trial/Confirm、风险和后续条件必须逐项覆盖；
- 每个对象必须具备角色、时点、趋势/历史位置、日内路径或极值、相对强弱、成交/承接、风险收益或资本效率、当前结论和下一条件；证据缺失时必须明确降级或fail-closed，不得用泛化句填充；
- 正文必须使用固定章节、独立段落和统一编号，不得追加交接审计、内部状态或第二正文。

正文身份固定为 request_id + decision_id + content_hash。同一身份只能有一份FINAL_CONTENT；重试只能沿同一notification_id重试投递，不得重新分析、改写正文或创建第二通知。BDS写入、BDS回读、notification_id、provider result和用户收达必须分别记录，任何后置workflow成功均不得替代provider成功或用户收达事实。

## 11.4 2026-10-15 Formal Decision故障域经验固化

本节将已通过代码、协议、测试和真实运行复核的经验固化为长期边界：

1. **先回复、后投影。** READY 后先冻结唯一 FINAL_CONTENT，并由用户窗口输出完整正文；BDS、completion、canonical review event、REPORT、notification 和 provider 均为后置投影，不得阻塞、缩短、改写或撤回正文。
2. **BDS不是通知唤醒源。** BDS只保存当前业务事实和冻结正文；通知只能由当前 Formal Decision completion 形成的 events/reviews/formal_<occurrence_id>.json 精确触发。
3. **事件必须精确绑定。** notification workflow 必须使用触发本次 completion 的精确事件路径、request/parent/decision identity 和 content_hash；缺少精确绑定时 fail-closed，禁止扫描历史目录、按文件名排序、按最新提交猜测或返回 ALREADY_MANAGED。
4. **旧路径永久失效。** events/decisions、BDS直投影、FORMAL_PRESENTATION_PATH、--formal-content-file、旧 REPORT/handoff 和 market-snapshot 被动唤醒均不得作为新 Formal Decision 生产路径；兼容代码只能显式拒绝或仅服务非 Formal 历史恢复。
5. **三层事实分别记录。** 正文生成、BDS写入/回读、completion event形成、notification形成、provider结果和用户实际收到必须分别记录；任何前一层不得冒充后一层。
6. **完整性不是字数替代。** 报告必须逐项覆盖同一 DWP 的全部问题、候选、观察对象、持仓、现金、可释放资本、Trial/Confirm和后续条件；字符长度只能作为质量观测，不能替代对象级覆盖，也不能用短报告绕过必答集合。
7. **修复必须验证相邻断点。** 每次通知故障修复至少检查：用户正文时序、completion owner、canonical event路径、精确 workflow 绑定、notification_center 投影、provider调用和历史/兼容 fallback；只修单个发送函数不构成闭环。
8. **真实验证与CI分离。** CI只证明代码合同；下一次自然 Formal Decision 才能验证真实定时入口、PushPlus provider结果和用户实际收达，未取得这些证据不得宣称生产通知闭环完成。

本节不新增 workflow、producer、state、writer、provider、通知链或交易权限；只固化既有 Formal Decision 和共享 REPORT/notification_center/PushPlus 合同。


### 6.2 后置投影的实际执行边界

“用户正文优先”是用户可见顺序，不是把交接动作推迟到最终消息返回之后。最终回复返回即结束当前Actor回合，不能假定同一回合仍会执行异步GitHub写入。因此，READY后必须先冻结唯一FINAL_CONTENT，再在最终用户消息返回前完成同一父请求的BDS写入/回读和FORMAL_DECISION_COMPLETION ingress提交；最后一条用户消息只能原文输出已经冻结的FINAL_CONTENT，不能先返回正文、再期待后置代码继续运行。

completion提交后，completion handoff 必须在同一执行回合把当前唯一 completion 路径交给既有 canonical owner，由该 owner 形成 events/reviews/formal_<occurrence_id>.json，再触发 decision-notification、notification_center 和 PushPlus。不得等待 market-snapshot 被动发现，不得由Actor直接写event、REPORT或notification。若BDS、completion或canonical event handoff失败，必须保留同一请求和唯一FINAL_CONTENT，并如实标记交接未完成；不得把“报告已生成”当作完成，不得声称通知成功，也不得创建第二请求、第二正文或旁路通知。

该边界同时适用于MANUAL_CONVERSATION、MANUAL_SCHEDULED_TASK和AUTOMATIC_SCHEDULED_TASK，不改变系统复核和交易复盘的已验证通知实现。

### Formal completion 唤醒合同（强制）

Formal Decision completion 通过 GitHub Contents 写入后，不得假定该 API 提交必然产生 Actions push workflow run。completion owner 必须将当前唯一 completion 文件路径作为 `formal_completion_path` 精确交给既有 `.github/workflows/decision-notification.yml` 的 `workflow_dispatch` 入口；该 workflow 只消费这一指定路径，调用 `process_state_sync_request.py` 形成唯一 `events/reviews/formal_<occurrence_id>.json`，再进入既有 `notification_center` 和 PushPlus。不得通过 BDS 唤醒、market-snapshot 被动唤醒、历史文件扫描、最近文件猜测或第二通知链兜底。workflow dispatch 只是唤醒方式，不改变 event owner、正文、身份、content_hash 或通知 owner；任一环节未形成当前 event，必须记录未验证，不得声称主动通知成功。


补充硬门：completion ingress不得由执行器手写成任意JSON。必须使用current main的completion builder，令request_id为<parent_request_id>__formal_decision_completion、parent_request_id为原始父请求，且文件名精确为<request_id>.json；三者任一不一致即拒绝进入canonical owner。completion写入并回读后，执行器必须显式调用既有decision-notification workflow的workflow_dispatch(formal_completion_path=<当前唯一completion路径>)；Contents push、BDS写入或market-snapshot均不构成唤醒。