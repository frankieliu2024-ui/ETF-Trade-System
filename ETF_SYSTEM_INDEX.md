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
- **Formal Decision执行域**：`docs/Formal Decision执行与用户回复协议.md`。负责Formal Decision直接消费current main固定时段事实、判断路径、业务回复时序和用户可见边界；不创建本次判断的新事实请求，不定义交易规则，不新增生产链。
- **生产变更与并发治理域**：`docs/生产变更与并发写入协议_V1.0.md`。负责生产变更准入、writer ownership、latest-main、并发、mutation gateway、状态producer和组合验收。`config/maintenance/production_mutation_protocol.json`只是该规范的机器可执行镜像，不是第二套规则源。

- **自动维护与运行保障横向协议**：`docs/ETF自动维护与运行保障协议_V1.0.md`。只负责自动运行场景的触发、唯一owner、幂等、回读、投影、失败分类和人工接管；不新增交易规则、数据规则、通知规则、Formal Decision业务规则、workflow、state或writer。机器镜像为`config/maintenance/automatic_maintenance_protocol.json`，不是第二套业务规则源。
- **任务入口与调度治理横向协议**：`docs/ETF任务入口与调度治理协议_V1.0.md`。只负责三个ETF任务的身份、自动/手动入口、节点、SKIP、版本保护和业务链路由；不复制业务规则，不新增任务、workflow、state或业务链。机器镜像为`config/maintenance/task_entry_scheduling_protocol.json`。

查询路由技术说明：`docs/市场行情查询路由与全球时点规则_V1.0.md`。它只解释ETF系统内部查询路由实现，不是第五个规范域；唯一无网络路由实现为`scripts/market_quote_router.py`，单对象查询入口为`scripts/query_market_object.py`，查询时即时补采由`scripts/query_time_market_refresh.py`执行。

## 2.1 三类任务当前冻结指针

三类任务的当前封存、字段映射、入口、场景、生产链、失败分类和恢复顺序，统一读取 `docs/freezes/ETF_THREE_TASKS_CURRENT_FREEZE.json`；目录说明见 `docs/freezes/README.md`。该清单不是第二规则源，若其 `freeze_status` 不是 `SEALED`，不得宣称平台 live Prompt/Schedule 已完成最终冻结。

## 3. 场景读取路由

### 正式ETF分析

Formal Decision通知交接使用 source=CHATGPT_SCHEDULED_REVIEW_ACTOR。\n\nFormal Decision不是市场事实生产任务。它不为本次判断创建新的请求，不创建MARKET_QUOTE_REFRESH，不要求父请求、request-bound快照、formal_reply_gate、query_context或decision_context，也不等待新的市场快照。它只消费current main已经形成的最近合法固定时段事实。

每次运行只建立本次运行身份：task_id、task_run_id、occurrence_id、requested_at_beijing、runtime_now、source、entry_mode、interaction_scenario、market_date、market_phase和判断事实边界。真实入口映射为：普通对话使用CHATGPT_MANUAL_FORMAL_ANALYSIS/MANUAL_CONVERSATION；任务界面手动运行使用CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS/MANUAL_SCHEDULED_TASK；系统自动运行使用CHATGPT_SCHEDULED_FORMAL_ANALYSIS/AUTOMATIC_SCHEDULED_TASK。平台未提供task_run_id或occurrence_id时，按本次运行身份生成，不得复用历史身份；这不单独阻断判断。

正式判断直接读取current main最近合法固定时段事实，并先判断快照路径：

- 盘中快照年龄不超过15分钟，标记为正式判断；
- 盘中快照年龄超过15分钟且不超过25分钟，标记为降级判断；
- 超过25分钟，标记为超时判断；三种路径均继续任务运行、正文交接和通知链，不因快照时间阻断。
- 收盘后优先使用当前交易日15:00、质量和PIT验收通过的正式收盘快照；采集时间距运行时间超过15分钟不自动降级；
- Discovery、观察ETF、机会ETF和实际持仓分别核验，不得互相替代；对象层缺失时可形成受限判断，但不得把缺失写成0或声称全市场比较已完成。

正式判断必须读取MASTER、数据与市场监测规范、账户与成交事实、风险与生命周期事实、Discovery、观察ETF、机会ETF、实际持仓和主动通知规范。事实足以判断时直接形成唯一FINAL_CONTENT；超时判断必须披露事实时点和限制，但不因此停止任务、正文交接或通知链。

正文冻结并立即发布后，completion的请求类型固定为 `request_type=SCHEDULED_REVIEW_COMPLETION`，source固定为 `CHATGPT_SCHEDULED_REVIEW_ACTOR`，并沿统一Scheduled Review链完成；不创建Formal专属completion、事件、报告或通知链。completion写入、completion回读、completion push、事件、REPORT、notification、服务商投递和用户收到必须分别核验，后置交接失败不得改写已形成的业务判断，也不得重新生成正文。

普通交易判断仍按MASTER和所属数据规范读取；用户上传的账户截图按既有账户事实入口处理，不改变Formal Decision的事实消费边界。

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


#### Formal Decision交接可执行性硬门

Formal Decision只使用统一Scheduled Review completion owner。completion沿用共享Scheduled Review路径，绑定本次运行身份、market_date、唯一FINAL_CONTENT和必要审计字段；不要求本次判断先建立父请求或request-bound事实链。正文冻结并发布后，继续写入并回读completion，再等待既有completion push通知链；只有在有界窗口内未形成或明确失败时，才使用同一completion进行灾备通知。各层级必须分别回读，不能把completion写入、通知workflow提交、服务商投递或历史通知表述为用户已收到。

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
