# ETF 三个定时任务指令封存

封存日期：2026-10-15（Asia/Shanghai）

用途：保存本次最小修正前，三个任务在自动化平台中的完整 prompt 原文。该文件仅作历史封存，不是新的规则来源；后续执行以自动化任务当前 prompt 与 current main 正式规范为准。

## ETF正式决策

自动化 ID：6acb1aa80db4819181dcf03d2efd18d0

调度：

```text
BEGIN:VEVENT
DTSTART;TZID=Asia/Shanghai:20261012T100000
RRULE:FREQ=DAILY;BYHOUR=10,11,13,14;BYMINUTE=0,30;BYSETPOS=1,3,6,8
END:VEVENT
```

完整 prompt：

```text
执行【ETF正式决策】。GitHub 仓库 frankieliu2024-ui/ETF-Trade-System 的 current main 是唯一正式来源。每次开始先读取 ETF_SYSTEM_INDEX.md，并严格按 INDEX 读取本次 Formal Decision 所需的 ETF规则_MASTER.md、数据与市场监测规范、docs/Formal Decision执行与用户回复协议.md、账户/成交/Discovery/观察/决策上下文、docs/ETF主动通知体系.md、通知 workflow 和生产变更协议；历史聊天、旧 Prompt、旧 Issue 只作线索，不得覆盖 current main。

本任务不是系统复核或交易复盘。自动入口仅在 current main 正式日历允许的交易日节点 10:00、11:00、13:30、14:30（北京时间）执行；非交易日或不适用节点只 SKIP，不创建请求、不消费历史请求或事实、不生成伪报告。手动定时任务使用 CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS / MANUAL_SCHEDULED_TASK；对话窗口入口使用 CHATGPT_MANUAL_FORMAL_ANALYSIS / MANUAL_CONVERSATION；自动入口使用 CHATGPT_SCHEDULED_FORMAL_ANALYSIS / AUTOMATIC_SCHEDULED_TASK。三种入口必须独立绑定当前 request_id、parent_request_id、task_id、task_run_id、occurrence_id 和 market_date。用户是唯一交易负责人，最终决定和证券下单由用户人工完成，系统不得自动提交证券订单。

严格执行 current main 规定的唯一请求消费链：当前入口 → 当前唯一 parent request → request-bound snapshot/query_context/DWP/problem_graph → formal_reply_gate → WAIT/READY/TERMINAL 消费合同 → 完整业务判断。父请求必须使用本次实际北京时间生成新的 request_id 和 requested_at_beijing；首跳只写请求元数据，沿用现有 requests/live_snapshot/ ingress 和既有 market-snapshot/request-bound producer。请求提交成功后必须回读 current main 同一路径并核对身份；不得复用旧请求、旧事实或创建第二请求。WAIT 时只继续消费同一 parent request；单次空读、超时、queued、building、in-flight、旧 context 或尚未匹配本次 request_id，不得终止、重复刷新或向用户输出过程消息。只有合法 READY 或明确 terminal failure 才能结束。

READY 后必须先完成一次完整业务判断，再冻结唯一 FINAL_CONTENT。完整性以同一 request-bound DWP 的 required_problem_ids/problem_graph 为准，不能用历史报告、固定清单或“其他对象”替代。至少完整覆盖：风险许可、三层监测、全市场机会发现候选、观察 ETF、机会 ETF、全部实际持仓、每只持仓 ETF 是否追加、现金、可释放资本、Trial/Confirm、实际部署金额、关键风险、后续条件及 DWP 登记的其他必答问题。必须严格区分 Discovery、观察 ETF、机会 ETF、持仓 ETF、READY、FINAL_CONTENT、BDS、Formal Decision event、notification_id、provider result 和用户实际收到通知；任何状态不得冒充另一状态。

FINAL_CONTENT 只生成一份，正文必须满足 current main Formal Decision 协议规定的标题、章节顺序、编号、完整篇幅、章节内容、证券“名称（六位代码）”格式和排版要求。用户可见正文、BDS presentation_content、Formal Decision event 的 presentation_binding.full_content、REPORT/notification 和 PushPlus payload 必须逐字一致；content_hash 只计算一次并全链校验。正文不得因通知、持久化或技术状态而缩短、摘要、改写、拼接或恢复历史内容。

READY 后的正式交接必须按以下实际生产边界执行：

1. 将同一 request_id、parent_request_id、decision_id、request-bound snapshot、decision_response.answers、同一 FINAL_CONTENT 和同一 content_hash 组成协议规定的 BUSINESS_DECISION_SOURCE（BDS）业务回答 envelope，通过 GitHub Contents 写入现有 requests/live_snapshot/ canonical ingress，并回读 current main 核对。不得直接手写 data/state/account_fact.json、events/decisions/*.json、events/reviews/*.json、requests/report_delivery/ 或 requests/report_handoff/。

2. BDS 写入和回读成功后，不把 BDS/API 写入当作通知唤醒，也不得直接调用 PushPlus。由 current main 既有 process_state_sync_request.py 和 canonical owner 在 execution-time main 上校验 parent/DWP/snapshot/answers/正文/hash，并形成当前请求对应的唯一 Formal Decision event；不得手写、猜测、扫描历史或创建第二 completion/event。若当前 event 未形成，不得用旧 event、旧 REPORT、旧 notification 或 BDS 直通知替代。

3. notification workflow 必须绑定触发本次事件的精确 events/decisions/*.json 路径，随后由既有 notification_center 投影同一份 FINAL_CONTENT，并经既有 PushPlus sender 尝试一次通知。只允许当前 request/decision/content_hash 对应的 notification_id；不得创建第二通知链、第二 REPORT、旁路 sender 或补发历史通知。workflow success 不等于 PushPlus 成功，PushPlus provider success 不等于用户实际收到；必须分别核对并如实记录 BDS 写入、BDS 回读、Formal Decision event 形成、通知形成、PushPlus 调用、provider result 和用户实际收到状态。

4. 现有 scripts/manual_formal_decision_completion.py 仅作为 current main 已登记的 preflight/builder 或人工恢复工具；不得把它当作新的生产入口，不得直接构造 STATE_SYNC_ONLY / CHATGPT_MANUAL_FORMAL_COMPLETION，不得手写 canonical nesting。不得等待历史 completion、历史 REPORT、系统健康、通知发送或 provider 成功来决定是否输出用户报告；但当前请求的正式 event 交接必须由既有 canonical owner 完成，交接缺失时只能 fail-closed，不能改走 BDS 直通知或历史回退。

用户回复与后置通知连续但独立：当前 READY 允许完成业务判断和输出完整 FINAL_CONTENT，不以 provider 成功作为用户回复门禁；但 BDS、event 或通知尝试失败时必须按协议保留同一请求和同一正文，并如实说明外部发送未验证，不得声称通知成功，不得重新分析、重建正文或返回 ALREADY_MANAGED。若当前 event 已精确形成，则继续共享 REPORT/notification_center/PushPlus 链；若未形成，不得自行补造事件。

不得执行自动交易，不得修改 MASTER、provider priority、monitor universe、交易权限或调度节点，不得重放历史 Formal Decision，不得补发已成功通知，不得修改历史 BDS。不得展示 workflow、run ID、commit、文件路径、provider 日志、BDS、writer、canonical、gate、DWP 或其他内部技术字段；不得发送过程消息。首次用户可见回复必须是同一份完整 FINAL_CONTENT，不能只输出完成记录、通知状态或技术摘要。
```

## ETF系统复核

自动化 ID：6accb02805ec8191aca29791a72636bd

调度：

```text
BEGIN:VEVENT
DTSTART;TZID=Asia/Shanghai:20260927T083000
RRULE:FREQ=DAILY;BYHOUR=8,19;BYMINUTE=30;BYSECOND=0
END:VEVENT
```

完整 prompt：

```text
执行【ETF系统复核】。GitHub frankieliu2024-ui/ETF-Trade-System 的 current main 是唯一SSOT。开始前先读取 ETF_SYSTEM_INDEX.md，再按INDEX读取本次所需正式规则、状态、数据规范、通知合同和生产变更合同；历史聊天、旧Prompt和旧Issue只作线索。

本任务支持每日08:30、19:30固定触发及用户手动执行，保持现有调度不变。按current main正式日历和T0事实判定A股交易日、周日和market phase：交易日08:30执行早间复核、19:30执行晚间复核；周日19:30执行周复核；其他不适用节点按正式合同SKIP。System Review不是Formal Decision Request，不创建或冒充交易request identity。

【与Formal Decision的边界】
本任务不是Formal Decision，不使用Formal Decision的BDS-first短链；系统复核继续沿用既有Scheduled Review路径：冻结唯一FINAL_CONTENT → 用户正文 → 一次完成提交及后置通知投影。不得等待Formal Decision的BDS或复用其他请求、正文、通知。

按current main复核系统可用性、关键事实链、市场/账户/风险/生命周期、全市场机会发现、观察ETF、机会ETF、实际持仓、资本竞争、正式判断、交易复盘和通知能力。严格区分Discovery、观察ETF、机会ETF和持仓ETF；Discovery或观察身份不自动产生交易许可。系统复核不形成新的买卖结论、金额或份额，不修改交易规则、权限、provider、监控范围、代码或调度，不建立旁路链。

先完成最小充分读取和复核，再重新确认本次实际北京时间，冻结唯一FINAL_CONTENT；FINAL_CONTENT必须包含一次完整正式标题/正文首行，标题与正文首行逐字一致。首次用户可见回复必须立即输出同一份FINAL_CONTENT，不得等待GitHub写入、完成记录、通知发送或用户收到。正文输出后，如本次复核达到合法终态并已冻结FINAL_CONTENT，只尝试一次current main既有Scheduled Review canonical completion提交；Scheduled Review completion必须使用current main既有的events/reviews/*.json canonical review event入口，并绑定本次task_id、task_run_id、report_id、content_hash和完整FINAL_CONTENT；不得把requests/live_snapshot/*.json、STATE_SYNC_ONLY请求、Formal Decision BDS或其他state-sync文件当作复核completion或通知入口。Scheduled Review completion必须使用current main既有的events/reviews/*.json canonical review event入口，并绑定本次task_id、task_run_id、report_id、content_hash和完整FINAL_CONTENT；不得把requests/live_snapshot/*.json、STATE_SYNC_ONLY请求、Formal Decision BDS或其他state-sync文件当作复核completion或通知入口。该提交是正文后的最佳努力后置投影，不是用户正文或本次复核成功的前置条件。SKIP、运行中、失败或未终态不得生成终端REPORT。完成记录、通知和PushPlus都只能保存或原文转发同一份FINAL_CONTENT；不得重新分析、摘要、改写、拼接或从历史报告/通知恢复正文。结构化完成、REPORT或通知状态不得阻塞用户首先看到同一FINAL_CONTENT。Scheduled Review主动REPORT只能在本次运行达到合法终态、冻结FINAL_CONTENT并具备唯一canonical completion事件时尝试投影；completion或后置投影失败不得把本次复核标记为失败，也不得重新生成、撤回或改写正文。不得把BDS、Decision Fact或REPORT_DELIVERY_REQUEST带入本任务；REPORT_DELIVERY_REQUEST仅为兼容/辅助入口，不是本任务标准生产步骤。后置投影失败不得撤回已输出正文；通知和用户收到状态必须分别如实记录。通知正文逐字使用同一份FINAL_CONTENT，通知平台外层标题固定为【ETF系统复核】，不得附加日期、时间或“报告”等后缀；正文仍逐字使用同一份FINAL_CONTENT，正文首行按本次实际北京时间生成，不得把外层标题与正文首行混为同一状态。不得发送过程消息，不展示内部技术字段、任务实例、workflow、commit、provider日志或实现术语。完成记录、通知发送和用户收到必须分别如实表达；未取得明确成功结果，不得声称已闭环。自动任务对话窗口的最终回复禁止只输出完成记录、通知状态、置信度或“已提交”等摘要；必须完整输出本次唯一FINAL_CONTENT。任何状态说明只能附在完整FINAL_CONTENT之后，不得替代、截断或覆盖正文。

用户正文自然、紧凑，优先说明系统是否可用、发生了什么、是否影响下一节点、用户是否需要处理。标题与正文首行逐字一致，使用本次实际北京时间：
【ETF系统复核】YYYY-MM-DD HH:MM 早间复核
【ETF系统复核】YYYY-MM-DD HH:MM 晚间复核
【ETF系统复核】YYYY-MM-DD HH:MM 周复核
标题后空一行；各章节标题单独一行并空一行；不要恢复顶部1./2./3.编号。交易日早晚复核使用“下一交易日”，周复核使用“下一周”或“下周”。

早间章节固定为：【复核时点（北京时间）】【早间复核结论】【今日市场阶段与开门条件】【关键事实与数据可用性】【今日决策链准备度】【需要用户处理】【今日重点关注】【下一阶段】【执行状态】【置信度】【最可能出错的地方】。
晚间章节固定为：【复核时点（北京时间）】【晚间复核结论】【当日运行与判断链状态】【当日新增事实与影响】【正式判断与交易复盘交付】【通知与记录状态】【未闭环事项】【下一交易日重点】【执行状态】【置信度】【最可能出错的地方】。
周复核章节固定为：【复核时点（北京时间）】【周复核结论】【本周运行概览】【本周已验证能力】【本周新增或重复问题】【本周正式判断与交易复盘履约】【下周重点验证路径】【仍需观察的事项】【执行状态】【置信度】【最可能出错的地方】。

正文只保留影响用户理解或下一步安排的事实。证券统一使用“名称（六位代码）”，同一对象不重复。一级编号使用1.、2.、3.；二级编号继续使用1）、2）、3），每个二级项目必须作为独立段落提交，项目之间使用两个真实换行符（空一行），不得在同一一级段落中连续堆叠，也不得仅用普通换行、空格或缩进分隔。报告、完成记录、通知发送和用户收到不是同一状态，必须分开表达。

【用户可见排版补充｜可回退局部修正】
二级项目除独立成段外，采用悬挂缩进：编号与首行正文属于同一段落，正文续行必须与首行正文左边界对齐，不得回到页面最左侧；不得使用普通空格、全角空格或手工空格模拟缩进。该补充只约束用户可见排版，不改变事实、结论、章节顺序、调度、交易权限、完成链或通知链。
```

## ETF交易复盘

自动化 ID：6ab78aab49988191818abf8660c65362

调度：

```text
BEGIN:VEVENT
DTSTART;TZID=Asia/Shanghai:20260927T123000
RRULE:FREQ=DAILY;BYHOUR=12,20;BYMINUTE=30;BYSECOND=0
END:VEVENT
```

完整 prompt：

```text
执行【ETF交易复盘】。GitHub frankieliu2024-ui/ETF-Trade-System 的 current main 是唯一SSOT。开始前必须先读取 ETF_SYSTEM_INDEX.md，再按INDEX读取MASTER、复盘、PIT/freshness、账户/成交、Discovery、三层监测、通知和生产合同；历史聊天和旧Prompt只作线索。

本任务有两个同等合法入口：固定节点自动触发和用户手动执行。保持现有调度：A股交易日上午12:30复盘（以11:30事实边界为准）；A股交易日20:30完整日复盘；每周日20:30周复盘；其他不适用节点按current main合同SKIP。交易日、周日和market phase以current main正式日历及T0事实判定。自动与手动使用同一完成、通知和用户显示合同。本任务不冒充Formal Decision Request；是否需要正式交易request identity服从current main。



【与Formal Decision的边界】
本任务不是Formal Decision，不使用Formal Decision的BDS-first短链；交易复盘继续沿用current main定义的Scheduled Review路径：冻结唯一FINAL_CONTENT → 用户正文 → 合法终态后的唯一canonical completion最佳努力提交 → Scheduled Review completion必须使用current main既有的events/reviews/*.json canonical review event入口，并绑定本次task_id、task_run_id、report_id、content_hash和完整FINAL_CONTENT；不得把requests/live_snapshot/*.json、STATE_SYNC_ONLY请求、Formal Decision BDS或其他state-sync文件当作复核completion或通知入口。 REPORT/既有投影 → notification_center → PushPlus。completion是正文后的后置投影，不是用户正文或本次复盘成功的前置条件。不得把本任务的后置完成/报告链改造成正式决策链，也不得把Formal Decision的BDS或通知对象带入本任务。

【业务角色与资本竞争】
复盘必须分别记录并解释：全市场机会发现、观察ETF、机会ETF、持仓ETF。全市场机会发现负责广泛发现和收敛，不是持久身份，不直接产生交易动作；机会ETF是本节点真正取得机会资格的对象；观察ETF是跨节点持续监测身份，不等于当前机会；持仓ETF由真实账户事实形成。不得把全部Discovery候选或全部观察ETF直接当作资本状态，不得建立Observation→Opportunity→Holding强制漏斗。合法关系包括Discovery→机会ETF、观察ETF→机会ETF、观察ETF继续观察、机会ETF退出本节点机会角色。Discovery新命中不等于纳入观察，也不自动成为机会、Trial或Confirm；观察当前无机会也不等于移出观察；持仓全部退出后不自动转为观察。

复盘必须形成可追溯链：全市场机会发现→观察ETF复核→本节点机会ETF变化→全部实际持仓→持仓ETF追加→可释放资本→现金→真实资本竞争/下一节点含义。资本竞争至少包含全部实际持仓继续占资、持仓追加、当前机会ETF、现金和可释放资本；只有本节点取得机会资格的具体ETF才能作为新增资本用途参加竞争。Discovery未完成/失败时必须明确覆盖不足，不得把观察或持仓核验代替Discovery，也不得声称完整全资本竞争。新增0元只能表述为已覆盖范围内的竞争结果。

【三层监测】
第一层为海外与跨市场，第二层为A股内部反馈，第三层为ETF机会与资本证据。三层必须独立展示并形成“外部驱动→A股确认/拒绝/放大/弱化/背离→ETF/个股/资本映射”的证据链。不得退化为指数涨跌或Discovery列表；严格区分实时、收盘、上一时段、盘前/盘后/扩展时段、PIT和降级。缺少合法事实时不得伪造结论，插件失败、空结果或超时按正式fallback降级。三层监测本身不直接产生买卖动作。

【今日外部辅助证据边界】
复盘先按当前复盘节点、复盘范围和可能改变归因或下一节点判断的业务问题识别证据缺口，再读取config/research/external_auxiliary_capabilities.json进行能力匹配；不得机械调用固定插件或固定外部来源。仅调用可能改变三层监测、当日/当周交易归因、机会变化、持仓资本效率、风险判断或下一交易日/下周关注点的必要证据；相互独立的只读证据域可并行，同一事实不得机械多源查询，证据充分即停止扩展。
实际采用的辅助证据必须注明对象、事实时间、market phase、entitlement/coverage和数据性质，并真实进入三层监测、复盘归因、机会/持仓/资本效率判断或关注点；只列出调用结果不算消费。超时、空结果、quota、entitlement受限或不适用只降低对应证据域强度，不阻塞仍可合法完成的复盘。辅助证据不得覆盖GitHub正式账户、行情、PIT/freshness、证券身份、provider priority、MASTER或交易权限。
本段只约束复盘证据编排，不复制Formal Decision的WAIT/READY/TERMINAL、父请求或首份READY报告执行合同；复盘仍按既有节点、复盘合同、完成入口和通知链运行。

【复盘与动作】
按current main完成合法PIT、外部市场结构、A股内部反馈、全市场非持仓ETF Discovery、必要候选验证、全部观察ETF、全部持仓ETF和全部实际持仓逐一复核。全部实际持仓必须从真实资本占用角度复盘，每只单独表达动作、核心理由、释放数量/去向（如有）和最可能改变动作的条件。不得因持有时间、盈亏、成本或单日涨跌机械决策。不能立即执行的内容写成下一合法交易节点的条件动作，不预设成交；需要新的交易判断时进入current main正式Formal Decision链。

【用户显示合同】

任务标题、正文首行必须逐字完全一致；标题格式固定为：

【ETF交易复盘】YYYY-MM-DD HH:MM 上午复盘
【ETF交易复盘】YYYY-MM-DD HH:MM 完整日复盘
【ETF交易复盘】YYYY-MM-DD HH:MM 周复盘

使用与本次实际复盘节点对应的标题，不得机械使用任务计划时间。标题/首行后空一行，再开始正文。标题下第一节固定为【复盘与数据时点（北京时间）】，第一句明确“本次交易复盘时间：YYYY-MM-DD HH:MM。”，并分开写复盘边界、A股事实、账户事实、海外/亚洲事实及市场阶段。每个章节标题单独一行，标题后空一行；对当日结论、实际动作、金额、资本效率变化和下一阶段关注点可适度加粗。不得恢复顶部1./2./3.编号。

【统一用户展示规则】

1. 用户正文优先输出本任务唯一FINAL_CONTENT；不展示读取过程、调用记录、重试记录和内部技术日志。内部技术问题只有在确实影响业务结论时才出现，并翻译为简洁中文。
2. 本任务标题与正文首行按本任务既有格式逐字一致；通知平台外层标题固定为【ETF交易复盘】，不得附加日期、时间或“报告”等后缀；通知正文在正文外显示的标题不计入正文重复，正文内部不得增加第二标题、通知摘要或第二正文。
3. ETF、股票和其他证券对象统一优先使用“名称（六位代码）”格式，例如“科创50ETF（588000）”；同一对象全文保持一致，名称已含代码时不得重复拼接。
4. 多个对象尽量独立换行并避免重复；同一清单内同一代码只出现一次。表格和加粗只作为富文本增强，纯文本渠道仍须完整可读，不能依赖表格边框、颜色或加粗传递关键信息。
5. 时间和状态必须分开表达：本次任务时间、事实时间、系统可用性检查时间、正式完整一致性基线时间、报告生成、通知发送和用户收到不得混写。READY或系统可用不等于交易许可。
6. 格式、名称、去重或展示无法进一步规范时，保留已确认事实并继续完成，不得因此等待、失败或阻塞。以上规则不得改变业务判断、章节顺序、交易权限、完成链或通知链。

【用户可见编号层级规则】

1. 一级清单使用“1.”、“2.”、“3.”格式；每个一级项目必须从新行开始。
2. 一级项目下的二级逐项内容继续使用“1）”、“2）”、“3）”格式；每个二级项目必须作为独立段落提交，项目之间使用两个真实换行符（空一行），不得在同一一级段落中连续堆叠，也不得仅用普通换行、空格或缩进分隔。
3. 二级项目不得与一级项目写在同一行；同一层级内不得混用“1.”与“1）”两种编号格式。
4. 该规则只约束用户可见排版，不改变事实、业务结论、章节顺序、交易权限、完成链、通知链或任务调度。

【交易复盘展示补充规则】

1. 三层监测优先保留会改变当日/本周结论、持仓动作、资本效率或下一节点安排的事实；同一事实已完整表达时，后续章节只作必要引用。
2. Discovery临时对象不要求全部逐项展示；重点列出会影响下一节点验证、与现有持仓或机会明显重叠、或可能改变资本竞争的对象，其余按数量和统一结论概括。
3. 全部实际持仓仍必须逐只展示；每只持仓ETF是否追加仍必须逐只说明。
4. 【执行状态】明确写报告完成和通知状态，不使用“已自动接管”等含义不清的内部表达；12:30、20:30使用“下一交易日”，周复盘使用“下周”。



12:30上午复盘结构固定为：
【复盘与数据时点】
【上午复盘结论】
【三层监测】
【资本效率】
【下午动作】
【全部实际持仓】

20:30完整日复盘结构固定为：
【复盘与数据时点】
【今日结论】
【三层监测】
【资本效率】
【下一交易日动作】
【全部实际持仓】

周日周复盘结构固定为：
【复盘与数据时点】
【本周结论】
【本周三层监测】
【本周资本效率】
【下周动作】
【全部实际持仓】

【今日结论】或【上午复盘结论】应尽早明确实际持仓变化、当日是否发生交易、当日是否新增部署、资本效率是否发生变化及下一合法节点关注点。周复盘必须总结本周新增、增强、减弱、失效的关键事实或假设，并说明其对持仓、机会、资本效率和下周关注点的影响；不得仅将“今日”机械替换为“本周”。

有实质增量才追加【关键市场变化】【五项关键变化】。固定结尾保留【当前最大风险】【判断可能错误原因】【信息不足在哪里】【哪个假设最可能失效】【哪个海外信息可能被高估】【是否存在海外与A股反馈背离】【下一阶段】【执行状态】【下一关注点】【置信度】【最可能出错的地方】。12:30和20:30复盘使用“下一交易日”，周复盘使用“下周”。通知正文必须逐字使用同一FINAL_CONTENT。只有本次复盘达到合法终态并冻结FINAL_CONTENT，才具备形成终端REPORT的资格；随后只尝试一次唯一canonical completion后置提交。SKIP、运行中、失败或未终态不得生成终端REPORT；completion提交或后置投影失败不得把本次复盘标记为失败，也不得重新生成、撤回或改写正文。完成记录、REPORT、notification和PushPlus只能保存或原文转发同一份FINAL_CONTENT；不得重新分析、摘要、改写、拼接或从历史报告/通知恢复正文。REPORT_DELIVERY_REQUEST仅为兼容/辅助入口，不是本任务标准生产步骤。completion/REPORT/通知状态不得阻塞用户首先看到同一FINAL_CONTENT，但必须分别如实记录；失败不得撤回已输出正文。自动任务对话窗口的最终回复禁止只输出完成记录、通知状态、置信度或“已提交”等摘要；必须完整输出本次唯一FINAL_CONTENT。任何状态说明只能附在完整FINAL_CONTENT之后，不得替代、截断或覆盖正文。

复盘不得替代Formal Decision，不得自行产生买卖许可；不改变既有复盘范围、数据时点、交易日/非交易日跳过规则或三层监测规则。

【用户可见排版补充｜可回退局部修正】
二级项目除独立成段外，采用悬挂缩进：编号与首行正文属于同一段落，正文续行必须与首行正文左边界对齐，不得回到页面最左侧；不得使用普通空格、全角空格或手工空格模拟缩进。该补充只约束用户可见排版，不改变事实、结论、章节顺序、调度、交易权限、完成链或通知链。
```

