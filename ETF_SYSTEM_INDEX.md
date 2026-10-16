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
- **Formal Decision执行域**：`docs/Formal Decision执行与用户回复协议.md`。负责Formal Decision的唯一父请求、WAIT/READY/TERMINAL三态消费、业务回复时序和用户可见边界；不定义交易规则，不新增生产链。  定时任务从入口身份、四节点调度、请求创建、后置交接和真实目标路径验收的独立合同读取 `docs/formal-decision-scheduled-task-rebuild-v2.md`。
- **生产变更与并发治理域**：`docs/生产变更与并发写入协议_V1.0.md`。负责生产变更准入、writer ownership、latest-main、并发、mutation gateway、状态producer和组合验收。`config/maintenance/production_mutation_protocol.json`只是该规范的机器可执行镜像，不是第二套规则源。

查询路由技术说明：`docs/市场行情查询路由与全球时点规则_V1.0.md`。它只解释ETF系统内部查询路由实现，不是第五个规范域；唯一无网络路由实现为`scripts/market_quote_router.py`，单对象查询入口为`scripts/query_market_object.py`，查询时即时补采由`scripts/query_time_market_refresh.py`执行。

## 3. 场景读取路由

### 正式ETF分析

三个合法入口统一读取并执行 `docs/Formal Decision执行与用户回复协议.md`：

|入口|source/trigger_source|entry_mode|规则|
|-|-|-|-|
|对话窗口|\`CHATGPT_MANUAL_FORMAL_ANALYSIS\`|\`MANUAL_CONVERSATION\`|用户明确发起即可运行|
|手动启动定时任务|\`CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS\`|\`MANUAL_SCHEDULED_TASK\`|用户明确启动即可运行，不受自动节点限制|
|自动节点启动定时任务|\`CHATGPT_SCHEDULED_FORMAL_ANALYSIS\`|\`AUTOMATIC_SCHEDULED_TASK\`|仅按current main正式日历、交易日和适用节点运行|

三个入口的业务判断、正文、完成事件、REPORT、通知和PushPlus必须使用同一条生产链：

`实际触发方式 → 本次唯一身份 → 当前正式事实 → 完整业务判断 → 唯一FINAL_CONTENT → STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW → canonical review event → REPORT → notification_center → PushPlus`

三个入口统一禁止把 \`MARKET_QUOTE_REFRESH\`、历史 \`formal_reply_gate\`、旧DWP、旧父请求、BDS 或 \`FORMAL_DECISION_COMPLETION\` 作为本次Formal Decision生产链。它们不能产生“对话一套链、定时任务另一套链”的分叉。具体合同只读取 `docs/Formal Decision执行与用户回复协议.md`；定时任务身份和节点细节补充读取 `docs/formal-decision-scheduled-task-rebuild-v2.md`。

盘前、集合竞价、盘中、午间、盘后、风险许可、生命周期、金额、卖出和资本比较：

`本INDEX → system_consistency → MASTER → 当前必要账户／行情／decision context → 所属规范或研究证据`

盘中执行最小充分读取。需要当前行情时，按数据规范和`config/runtime_policy.json`判断当前场景，优先消费本次请求后的第一份合格正式行情；没有才触发一次查询时即时补采。同一用户请求不得重复创建第二条等价补采链。正式回复前最后复核一次当前正式行情，已完成的新行情必须纳入本次回复。

普通无新增成交的盘中券商截图可直接作为本次最新账户事实参与判断，不等待Dashboard持久化；真实成交、资金划转或其他会改变持仓／现金／生命周期的账户事件必须先闭环必要账户事实。

### 券商截图自动路由

用户上传可读的券商账户／持仓／成交截图时，截图本身即构成账户事实输入，不以是否显式`@GitHub`作为是否维护的条件，也不询问用途。ChatGPT必须在回复“已查收／已更新”或继续依赖该账户事实前，先把截图中可确认的账户事实提交到既有`requests/live_snapshot/*.json` canonical ingress，使用`source=CHATGPT_USER_BROKER_SCREENSHOT`并保留真实截图／提交时间；随后回读current main同一request，核对request identity和关键账户事实。截图事实转换必须满足既有ingress的强类型账户合同：`positions`逐项为具名对象并以已确认证券代码作为身份字段，不得用位置数组、名称猜测或聊天记忆代替机器字段；正式owner在账户merge前执行schema fail-fast。不得只在聊天中解释截图而跳过ingress，也不得直接手写正式`account_fact`状态文件或Dashboard。

若ingress提交或回读失败且无法确认，必须明确停在“账户事实尚未持久化”的真实边界，不得声称已维护。真实成交事件优先于时段路由；具体`interaction_scenario`、时间窗口和当前运行参数统一读取`config/runtime_policy.json`，不在INDEX维护第二份时间表。15:00后当天首次最终账户截图按runtime policy进入既有正式收盘复盘链；同日重复截图仅做差异维护。截图提交时间与市场行情时间必须分离。

### 系统维护、故障和生产变更

`本INDEX → 当前main → docs/生产变更与并发写入协议_V1.0.md → config/maintenance/production_mutation_protocol.json → 相关canonical owner／脚本／workflow → 组合验收`

Codex协作交接说明：`docs/Codex协作执行说明.md`。它只负责CODEX_EXECUTION_BRIEF与执行结果的GitHub持久化和回读路由，不复制生产治理或交易规则。

先执行生产变更准入判断；用户要求修复不自动等于允许修改。需要修改时必须基于最新`main`隔离实施，不force push，最终执行full consistency与E2E组合验收。

## 4. 运行事实与配置入口

以下均是运行事实或实现入口，不是新的规则来源：

- 系统一致性：`data/state/system_consistency.json`、`scripts/check_system_consistency.py`、`.github/workflows/system-consistency.yml`
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

研究文件存在不等于正式转化。哪些研究允许进入执行及稳定使用边界读取MASTER第6.4；研究如何取得正式资格读取MASTER第8.1；动态研究数值读取`research_execution_summary.json`及对应证据文件。

## 4.1 用户可见业务语义

ETF系统面向用户的业务表达默认统一为：**持仓ETF、观察ETF、机会ETF、全市场机会发现**。观察ETF表示持续但可替换的信息监测身份；机会ETF表示节点级动态机会角色，不建立第三个持久池。观察与机会可在同一节点重叠，持仓身份只由账户事实形成。内部实现可继续使用既有兼容字段、枚举和函数名；除非这些内部名称本身造成业务语义错误，否则不得仅为展示一致而进行schema迁移。正式身份、交易权限和准入/退出标准仍只由MASTER定义。

## 5. 一致性与权威边界

INDEX只负责回答“去哪里读”，不得重新定义以下内容：正式指数全集、ETF监测全集、provider优先级、runtime参数、交易时段、通知阈值、生产变更准入规则、研究转化标准或交易权限。上述内容分别由所属规范和机器事实承载。

系统一致性检查必须验证正式文件入口、规范域入口、关键机器配置／状态、三层监测、研究登记、writer ownership、生产治理镜像、workflow链和E2E可用性。`PASS`表示本报告登记的生产一致性合同已实际通过，不代表系统已被全面证明没有未覆盖错误；`WARNING`表示可解释的降级；`FAIL`表示硬冲突，不能把当前云端结构视为完整生产状态。

任何实现与所属唯一规范冲突时，应修复实现或规范镜像漂移；不得让INDEX、配置、脚本或历史聊天反向创造新的规则。README同样只承担导航和运行说明，不复制正式版本号、provider优先级或其他易变事实。


#### Formal Decision三入口统一交接硬门

三个Formal入口只允许使用统一的 Scheduled Review 生产合同：

实际入口身份 → 当前正式事实 → 完整业务判断 → 唯一FINAL_CONTENT → 用户正文 → `STATE_SYNC_ONLY + FORMAL_SCHEDULED_REVIEW` → canonical review event → REPORT → notification_center → PushPlus 原文投递。

统一请求必须由 current main 的 Scheduled Review completion builder 生成，并包含：

- `request_type=STATE_SYNC_ONLY`；
- `formal_fact_type=FORMAL_SCHEDULED_REVIEW`；
- `source=CHATGPT_SCHEDULED_REVIEW_ACTOR`；
- 实际 `trigger_source` 与 `entry_mode`；
- `interaction_scenario=FORMAL_DECISION`；
- 与本次运行绑定的 `task_id`、`task_run_id`、`occurrence_id`、`request_id`、`market_date`；
- `presentation_binding.report_type=ETF_FORMAL_DECISION`、完整 `full_content` 和唯一 `content_hash`。

三个入口不得创建、消费或转换 `MARKET_QUOTE_REFRESH`、历史 `formal_reply_gate`、旧DWP、`BUSINESS_DECISION_SOURCE/BDS`、`FORMAL_DECISION_COMPLETION` 或其他第二请求。旧对象只能作为历史数据留存，不能成为当前Formal入口的生产路径。用户正文优先只规定显示顺序；completion、review event、REPORT和通知均必须绑定同一份已冻结正文，不能在正文之外重新分析、摘要或改写。

这一硬门只约束【ETF正式决策】三个入口，不改变【ETF系统复核】和【ETF交易复盘】既有链。
