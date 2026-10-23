# ETF-Trade-System 统一读取入口

本文件是仓库**路由索引**，不是交易规则、数据规范、通知规范或生产治理规范。GitHub `main` 是云端唯一主版本；ChatGPT、Codex 和 GitHub Actions 均先从本索引确定正式读取路径，再到所属唯一规范或机器事实读取内容。

## 1. 五个正式文件

|职责|唯一路径|读取目的|
|-|-|-|
|交易规则|`ETF规则_MASTER.md`|系统允许什么；风险许可、Trial／Confirm、金额、卖出、研究正式转化边界|
|当前状态|`ETF当前状态_DASHBOARD.md`|当前账户、生命周期和最近正式状态；规则不在此定义|
|经验复盘|`ETF交易复盘与经验库_2026.md`|CASE、OBS、复盘与历史决策原因|
|行情事实|`ETF市场行情档案_2026.md`|客观行情、成交、截图事实、来源与质量记录|

正式优先级：MASTER > Dashboard > 当前有效行情与实际成交 > 经验库 > 行情档案 > 历史聊天。历史聊天不属于正式来源。

## 2. 五个规范域

系统只保留以下五个独立规范域；各规范文件分别是所属域的**唯一规范性规则来源**。INDEX只负责路由，不复制其正文、对象清单、阈值、时段表或当前参数：

- **交易域**：`ETF规则_MASTER.md`。
- **数据与市场监测域**：`ETF与市场监测数据接口使用规范.md`。负责数据资格、三层监测语义、market phase、Point-in-Time、provider降级、代理、新鲜度、质量验收与查询时补采边界。当前对象、provider顺序和runtime参数读取机器配置，不从INDEX复制。
- **主动通知域**：`docs/ETF主动通知体系.md`。负责通知资格、事件语义、标题、固定节点、可比事实门、去重、恢复和展示边界。
- **Formal Decision执行域**：`docs/Formal Decision执行与用户回复协议.md`。负责Formal Decision的唯一父请求、WAIT/READY/TERMINAL三态消费、业务回复时序和用户可见边界；不定义交易规则，不新增生产链。
- **生产变更与并发治理域**：`docs/生产变更与并发写入协议_V1.0.md`。负责生产变更准入、writer ownership、latest-main、并发、mutation gateway、状态producer和组合验收。`config/maintenance/production_mutation_protocol.json`只是该规范的机器可执行镜像，不是第二套规则源。

- **自动维护与运行保障横向协议**：`docs/ETF自动维护与运行保障协议_V1.0.md`。只负责自动运行场景的触发、唯一owner、幂等、回读、投影、失败分类和人工接管；不新增交易规则、数据规则、通知规则、Formal Decision业务规则、workflow、state或writer。机器镜像为`config/maintenance/automatic_maintenance_protocol.json`，不是第二套业务规则源。
- **任务入口与调度治理横向协议**：`docs/ETF任务入口与调度治理协议_V1.0.md`。只负责三个ETF任务的身份、自动/手动入口、节点、SKIP、版本保护和业务链路由；不复制业务规则，不新增任务、workflow、state或业务链。机器镜像为`config/maintenance/task_entry_scheduling_protocol.json`。

查询路由技术说明：`docs/市场行情查询路由与全球时点规则_V1.0.md`。它只解释ETF系统内部查询路由实现，不是第五个规范域；唯一无网络路由实现为`scripts/market_quote_router.py`，单对象查询入口为`scripts/query_market_object.py`，查询时即时补采由`scripts/query_time_market_refresh.py`执行。

## 2.1 三类任务当前冻结指针

三类任务的当前封存、字段映射、入口、场景、生产链、失败分类和恢复顺序，统一读取 `docs/freezes/ETF_THREE_TASKS_CURRENT_FREEZE.json`；目录说明见 `docs/freezes/README.md`。该清单不是第二规则源，若其 `freeze_status` 不是 `SEALED`，不得宣称平台 live Prompt/Schedule 已完成最终冻结。

## 3. 场景读取路由

### 正式ETF分析

#### 用户发起的新 Formal Decision Request：父请求首跳 ingress（强制）

当用户新发起当前交易判断，或明确要求按当前时点重新判断时，必须先为这次用户请求建立独立父请求身份，再读取任何用于本次正式判断的账户、行情、Discovery 或 decision context。恢复同一条仍在处理中的请求时沿用其父请求身份；新的用户触发必须创建新身份。System Review、scheduled review、REPORT 交付、普通行情问答不属于 Formal Decision Request，不得创建或冒充该身份。

1. 以本次实际触发方式确定入口身份，再以本次实际北京时间生成唯一 `request_id` 和 `requested_at_beijing`。对话窗口使用 `source=CHATGPT_MANUAL_FORMAL_ANALYSIS`、`entry_mode=MANUAL_CONVERSATION`；任务界面手动启动使用 `source=CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS`、`entry_mode=MANUAL_SCHEDULED_TASK`；系统自动节点使用 `source=CHATGPT_SCHEDULED_FORMAL_ANALYSIS`、`entry_mode=AUTOMATIC_SCHEDULED_TASK`。把只含请求元数据的父请求 envelope 写入现有 `requests/live_snapshot/<request_id>.json`，统一使用 `request_type=MARKET_QUOTE_REFRESH`、`intent.query_intent=FORMAL_INTRADAY_ANALYSIS`、`force_refresh=true`、`require_post_request_snapshot=true`、`reuse_inflight_refresh=true`、`duplicate_equivalent_refresh_forbidden=true`。不得把任务界面手动启动或自动节点改写为对话入口，也不得在首跳 envelope 中填写交易动作或把它伪装成 `FORMAL_DECISION` 事实。
2. 使用已连接 GitHub 的 Contents `create_file` 能力将该 envelope 提交到 current `main`；请求文件写入是本次运行 ingress，不是代码变更，不走分支或 PR。必须确认工具返回提交回执，并回读 current `main` 上同路径文件，核对 `request_id` 与 `requested_at_beijing`。若文件已存在，先核对是否属于同一请求；新请求必须使用不同身份。若提交失败或结果不确定且无法回读确认，停止正式分析，不得用旧请求、当前状态或直接行情替代。
3. 由该请求文件的 push 唤醒现有 `.github/workflows/market-snapshot.yml` 和既有 request-bound producer；不得另建 workflow、producer、state 或第二决策链。按当前场景和数据规范取得本次 request-bound 合法事实。
4. 正式推理前必须先读取小型 `data/state/e2e_status.json` 的 `components.decision_context.formal_reply_gate`；该既有gate必须由 `scripts/build_e2e_status.py` 直接复用 `scripts/formal_request_consumer.py` 的 `classify_formal_request_consumer_state` 投影 `consumer_status`、`actor_action`、`user_output_allowed` 及READY必答清单，外部Actor不得自行重解释 `reply_freezable`。其 `actor_action` 与 `user_output_allowed` 是Formal Decision协议登记的Actor执行合同。`CONTINUE_SAME_REQUEST_SILENTLY`／`user_output_allowed=false` 时属于硬用户可见门禁，ChatGPT不得输出等待、处理中、外部条件或失败状态，只能继续消费同一父请求。随后读取小型 `data/state/e2e_status.json` 的 `components.decision_context.formal_reply_gate` 作为消费侧首要机器门：其 `request_id` 必须与本次父请求一致，且 `reply_freezable=true`；若 request_id 仍旧/缺失或状态为 IN_FLIGHT/BUILDING，只表示同一请求仍在形成，必须继续消费同一链，不得终止或创建第二请求。门通过后，再确认 `data/state/query_context.json` 的 `decision_fact_pack.trigger.request_id` 与本次父请求 ID 完全一致，且其中 `decision_work_package.problem_graph` 属于本次请求。缺少或不匹配时保持 fail-closed；不得把无身份的当前 DWP 或历史 DWP 当成本次请求包。**父请求已成功写入并回读后，若当前 `query_context` 仍为空、属于旧请求或尚未匹配本次 `request_id`，该现象本身只表示本次 request-bound facts 尚未发布，不构成 Formal Decision 的终止失败。此时必须沿同一父请求检查现有 canonical workflow/check 状态并继续消费同一链；只要该链仍为 queued/in_progress/building，就不得创建第二个等价请求，也不得向用户输出最终失败结论。只有本次同 request 的 `formal_reply_freeze.reply_freezable=true`／合法 READY（含明确允许的 resolved degradation），或现有 canonical 链明确进入 terminal failure／不存在合法链时，才允许结束等待并进入业务判断或 fail-closed 终止。对于大型 canonical JSON 的连接器空读，必须先以同一 GitHub Contents 来源回读/复核后才能认定缺失。**
#### Formal Decision 回复优先与用户可见边界（强制）

本场景唯一三态合同读取 `docs/Formal Decision执行与用户回复协议.md`；本节只提供执行路由，不复制该协议正文。

当 `formal_reply_freeze.reply_freezable=true` 且本次请求绑定事实一致时，ChatGPT必须先冻结唯一 FINAL_CONTENT 并完成正文校验；随后通过统一 Scheduled Review completion owner 写入并回读 completion，再由 canonical owner 形成本次运行唯一 event、REPORT 和通知。正式决策不创建或依赖 BDS，不使用专属完成器，不扫描历史对象。后置交接成功或明确到达最后失败层级后，才向用户输出同一份完整FINAL_CONTENT，并附真实交接状态。后置交接不得重新分析、改写或生成第二份正文；失败或超时必须记录后置未完成状态并输出同一正文。
正式业务回复只表达业务结论、依据、风险和人工交易边界；不得出现 `canonical`、`writer`、`BDS`、`reply_freezable`、`control plane`、内部 workflow/job、持久化等待或“自动接管”等内部或歧义表述。默认用户可见语义为“本次正式判断已完成；证券交易仍由用户人工确认和下单”。

该规则不缩短长报告、不新增状态或第二条决策链；它只规定 facts达到可判断状态后的执行顺序和展示边界。

5. 完成业务判断后，先冻结唯一 FINAL_CONTENT；随后调用 current main 登记的 Formal Decision Actor，由共享 Scheduled Review completion owner 写入并回读唯一完成记录，再由 canonical owner 形成当前 `events/reviews/formal_<occurrence_id>.json`，进入 REPORT、notification_center 和 PushPlus 主链。不得创建任何中间业务决策对象，不得由 Actor直接手写event、REPORT或通知文件，不得扫描历史对象或创建第二条通知链。后置交接必须有界等待；交接成功或明确到达最后失败层级后，用户窗口才输出此前冻结的同一份完整FINAL_CONTENT。后置失败不得重新分析、改写或生成第二份正文；provider成功不等于用户已收到。

Formal Decision 的统一交接字段为：`request_type=SCHEDULED_REVIEW_COMPLETION`、`source=CHATGPT_SCHEDULED_REVIEW_ACTOR`、`formal_fact_type=FORMAL_POST_CLOSE_REVIEW`、`interaction_scenario=FORMAL_DECISION` 和 `presentation_binding.report_type=ETF_FORMAL_DECISION`。completion必须绑定同一父请求、同一运行身份、同一market_date、同一FINAL_CONTENT和正文哈希。GitHub Contents写入只用于既有请求或完成记录入口，不执行投影、不生成交易判断；事件、报告和通知只能由既有canonical owner生成。

盘前、集合竞价、盘中、午间、盘后、风险许可、生命周期、金额、卖出和资本比较：

`本INDEX → system_consistency → MASTER → 当前必要账户／行情／decision context → 所属规范或研究证据`

盘中执行最小充分读取。需要当前行情时，按数据规范和`config/runtime_policy.json`判断当前场景，优先消费本次请求后的第一份合格正式行情；没有才触发一次查询时即时补采。同一用户请求不得重复创建第二条等价补采链。正式回复前最后复核一次当前正式行情，已完成的新行情必须纳入本次回复。

普通无新增成交的盘中券商截图可直接作为本次最新账户事实参与判断，不等待Dashboard持久化；真实成交、资金划转或其他会改变持仓／现金／生命周期的账户事件必须先闭环必要账户事实。

### 券商截图自动路由

用户上传可读的券商账户／持仓／成交截图时，截图本身即构成账户事实输入，不以是否显式`@GitHub`作为是否维护的条件，也不询问用途。ChatGPT必须在回复“已查收／已更新”或继续依赖该账户事实前，先把截图中可确认的账户事实提交到既有`requests/live_snapshot/*.json` canonical ingress，使用`source=CHATGPT_USER_BROKER_SCREENSHOT`并保留真实截图／提交时间；随后回读current main同一request，核对request identity和关键账户事实。截图事实转换必须满足既有ingress的强类型账户合同：`positions`逐项为具名对象并以已确认证券代码作为身份字段，不得用位置数组、名称猜测或聊天记忆代替机器字段；正式owner在账户merge前执行schema fail-fast。不得只在聊天中解释截图而跳过ingress，也不得直接手写正式`account_fact`状态文件或Dashboard。

若ingress提交或回读失败且无法确认，必须明确停在“账户事实尚未持久化”的真实边界，不得声称已维护。真实成交事件优先于时段路由；具体`interaction_scenario`、时间窗口和当前运行参数统一读取`config/runtime_policy.json`，不在INDEX维护第二份时间表。15:00后当天首次最终账户截图按runtime policy进入既有正式收盘复盘链；同日重复截图仅做差异维护。截图提交时间与市场行情时间必须分离。

### 系统维护、故障和生产变更

自动维护场景先读取`docs/ETF自动维护与运行保障协议_V1.0.md`确定触发、owner、回读和失败合同，再按本节生产变更协议判断是否需要修改代码或workflow。该协议只做横向运行路由，不覆盖所属业务规范。
任务启动、自动/手动入口、节点适用性和SKIP先读取`docs/ETF任务入口与调度治理协议_V1.0.md`；该协议只做任务路由，不覆盖System Review、Trade Review或Formal Decision业务协议。

`本INDEX → 当前main → docs/生产变更与并发写入协议_V1.0.md → config/maintenance/production_mutation_protocol.json → 相关canonical owner／脚本／workflow → 组合验收`

Codex协作交接说明：`docs/Codex协作执行说明.md`。它只负责CODEX_EXECUTION_BRIEF与执行结果的GitHub持久化和回读路由，不复制生产治理或交易规则。

先执行生产变更准入判断；用户要求修复不自动等于允许修改。需要修改时必须基于最新`main`隔离实施，不force push，最终执行full consistency与E2E组合验收。

## 4. 运行事实与配置入口

以下均是运行事实或实现入口，不是新的规则来源：

- 系统一致性：`data/state/system_consistency.json`、`scripts/check_system_consistency.py`、`.github/workflows/etf-system-consistency.yml`
- 当前正式行情：`data/state/CURRENT.json`
- 账户事实：`data/state/account_fact.json`
- 运行健康：`data/state/runtime_health.json`、`data/state/overseas_runtime_health.json`
- 当前决策证据：`data/state/decision_context.json`
- 查询上下文：`data/state/query_context.json`
- 收盘复盘上下文：`data/state/review_context.json`、`post_market_review/post_market_review_event.json`
- 实时相邻变化：`data/state/market_delta.json`
- 日内路径：`data/state/intraday_path_features.json`、`scripts/build_intraday_path_features.py`
- 海外／亚洲正式上下文：`data/state/overseas_context.json`
- 美股扩展时段：`data/state/us_extended_hours_context.json`、`scripts/build_us_extended_hours_context.py`、`.github/workflows/us-extended-hours-pulse.yml`
- 三层监测机器配置：`config/market/market_monitor_config.json`
- ETF机器运行全集：`config/market/etf_monitor_universe.json`
- provider优先级：`config/market/provider_priority.json`
- A股交易日历：`config/market/a_share_trading_calendar_2026.json`
- 运行策略和场景路由：`config/runtime_policy.json`
- 动态个股角色：`data/state/asset_roles.json`、`data/state/stock_context.json`、`data/state/stock_market_context.json`
- 研究执行桥：`data/state/research_execution_summary.json`
- 外部辅助证据能力提示：`config/research/external_auxiliary_capabilities.json`（只读Actor能力登记；不是provider优先级、生产state、DWP producer或交易权限来源）
- 查询时补采请求：`requests/live_snapshot/*.json`
- 历史行情事实恢复合同：`scripts/historical_market_fact_recovery.py`（只读评估；正式写入仍归现有market-data writer）
- A股行情workflow：`.github/workflows/market-snapshot.yml`
- 海外盘前workflow：`.github/workflows/overseas-preopen-pulse.yml`
- 正式事实低层写入：`scripts/formal_file_mutation_gateway.py`
- 自动维护场景与owner机器镜像：`config/maintenance/automatic_maintenance_protocol.json`
- 任务入口与调度机器镜像：`config/maintenance/task_entry_scheduling_protocol.json`

研究文件存在不等于正式转化。哪些研究允许进入执行及稳定使用边界读取MASTER第6.4；研究如何取得正式资格读取MASTER第8.1；动态研究数值读取`research_execution_summary.json`及对应证据文件。

## 4.1 用户可见业务语义

ETF系统面向用户的业务表达默认统一为：**持仓ETF、观察ETF、机会ETF、全市场机会发现**。观察ETF表示持续但可替换的信息监测身份；机会ETF表示节点级动态机会角色，不建立第三个持久池。观察与机会可在同一节点重叠，持仓身份只由账户事实形成。内部实现可继续使用既有兼容字段、枚举和函数名；除非这些内部名称本身造成业务语义错误，否则不得仅为展示一致而进行schema迁移。正式身份、交易权限和准入/退出标准仍只由MASTER定义。

## 5. 一致性与权威边界

INDEX只负责回答“去哪里读”，不得重新定义以下内容：正式指数全集、ETF监测全集、provider优先级、runtime参数、交易时段、通知阈值、生产变更准入规则、研究转化标准或交易权限。上述内容分别由所属规范和机器事实承载。

系统一致性检查必须验证正式文件入口、规范域入口、关键机器配置／状态、三层监测、研究登记、writer ownership、生产治理镜像、workflow链和E2E可用性。`PASS`表示本报告登记的生产一致性合同已实际通过，不代表系统已被全面证明没有未覆盖错误；`WARNING`表示可解释的降级；`FAIL`表示硬冲突，不能把当前云端结构视为完整生产状态。

任何实现与所属唯一规范冲突时，应修复实现或规范镜像漂移；不得让INDEX、配置、脚本或历史聊天反向创造新的规则。README同样只承担导航和运行说明，不复制正式版本号、provider优先级或其他易变事实。


#### Formal Decision交接可执行性硬门（集中修复）

当前正式决策只允许使用统一 Scheduled Review completion owner。completion必须使用current main登记的统一构建器生成：`request_id=<parent_request_id>__scheduled_review_completion`，`parent_request_id=<原始父请求>`，且文件名必须精确为`<request_id>.json`；不得手写、复用父请求ID或使用任意文件名。写入并回读completion后，由统一canonical owner形成本次运行唯一正式决策event并进入notification_center；不得把历史对象或GitHub Contents push本身当作通知提交。

“冻结正文、完成交接后输出”定义用户可见顺序：ChatGPT先冻结唯一FINAL_CONTENT，随后由同一运行完成统一SCHEDULED_REVIEW_COMPLETION的写入、回读和既有workflow投影；交接成功或明确记录最后失败层级后，再输出此前冻结的同一FINAL_CONTENT。completion、event、REPORT、notification_center和PushPlus必须继续使用同一运行身份和正文哈希；后置链路不得无限等待、改写或生成第二正文，失败必须记录PROJECTION_INCOMPLETE或MAINTENANCE_INCOMPLETE。

只生成完整正文但未完成completion回读和后置交接，不得视为本次Formal Decision完整完成；不得以“后置异步”“已生成报告”或其他状态替代交接。若交接失败，必须保留同一请求和正文并明确记录未完成，不得声称主动通知已发送。该硬门适用于对话窗口、手动定时和自动定时三个入口，不改变【ETF系统复核】和【ETF交易复盘】既有链。

## 6. 冻结、封存与回退登记

三个ETF任务的冻结、历史归档、版本追踪和回退关系唯一登记于：

`docs/freezes/archive/registries/ETF三类任务冻结注册表.json`

该注册表只负责冻结登记、审计和回退索引，不复制交易规则、业务协议、任务Prompt、workflow、state、入口、场景或通知规则，不构成第二规则源。当前live任务配置完成逐项回读并登记前，不能宣称三类任务已完成最终冻结。

历史冻结全文仅用于审计和回退：

- `docs/freezes/archive/legacy/ETF三个定时任务冻结封存_2026-10-16.md`
- `docs/freezes/archive/legacy/ETF三个定时任务冻结封存_2026-10-16-v2.md`
- `docs/freezes/archive/legacy/formal-decision-scheduled-task-rebuild-v2.md`

不得从历史归档、分支临时清单或聊天记录读取当前运行规则；当前规则仍按本索引路由至正式协议、机器事实和live任务配置。


## Scheduled Review显式交接硬门

【ETF系统复核】与【ETF交易复盘】完成唯一FINAL_CONTENT和对应的 Scheduled Review completion 写入、回读后，必须在同一handoff中显式调用既有 `decision-notification.yml` 的 `workflow_dispatch`，并传入 `scheduled_review_completion_path=<当前唯一completion路径>`。不得依赖GitHub Contents push、`GITHUB_TOKEN`提交、market-snapshot或其他被动唤醒，因为这些方式不保证创建通知工作流运行。

当前运行只有同时满足以下条件，才可声明后置交接完成：completion写入并回读；显式通知workflow运行已创建并回读；canonical review event、REPORT、notification状态和provider结果均与同一task_run_id、report_id、content_hash及FINAL_CONTENT一致。否则必须保留正文并记录最后完成层级及具体未完成层级，不得以“无更新”、定时任务平台completed或历史通知替代本次结果。
