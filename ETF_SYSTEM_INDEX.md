# ETF-Trade-System 统一读取入口

本文件是仓库**路由索引**，不是交易规则、数据规范、通知规范或生产治理规范。GitHub `main` 是云端唯一主版本；ChatGPT、Codex 和 GitHub Actions 均先从本索引确定正式读取路径，再到所属唯一规范或机器事实读取内容。

## 1. 四个正式文件

|职责|唯一路径|读取目的|
|-|-|-|
|交易规则|`ETF规则_MASTER.md`|系统允许什么；风险许可、Trial／Confirm、金额、卖出、研究正式转化边界|
|当前状态|`ETF当前状态_DASHBOARD.md`|当前账户、生命周期和最近正式状态；规则不在此定义|
|经验复盘|`ETF交易复盘与经验库_2026.md`|CASE、OBS、复盘与历史决策原因|
|行情事实|`ETF市场行情档案_2026.md`|客观行情、成交、截图事实、来源与质量记录|

正式优先级：MASTER > Dashboard > 当前有效行情与实际成交 > 经验库 > 行情档案 > 历史聊天。历史聊天不属于正式来源。

## 2. 四个规范域

系统只保留以下四个独立规范域；各规范文件分别是所属域的**唯一规范性规则来源**。INDEX只负责路由，不复制其正文、对象清单、阈值、时段表或当前参数：

- **交易域**：`ETF规则_MASTER.md`。
- **数据与市场监测域**：`ETF与市场监测数据接口使用规范.md`。负责数据资格、三层监测语义、market phase、Point-in-Time、provider降级、代理、新鲜度、质量验收与查询时补采边界。当前对象、provider顺序和runtime参数读取机器配置，不从INDEX复制。
- **主动通知域**：`docs/ETF主动通知体系.md`。负责通知资格、事件语义、标题、固定节点、可比事实门、去重、恢复和展示边界。
- **生产变更与并发治理域**：`docs/生产变更与并发写入协议_V1.0.md`。负责生产变更准入、writer ownership、latest-main、并发、mutation gateway、状态producer和组合验收。`config/maintenance/production_mutation_protocol.json`只是该规范的机器可执行镜像，不是第二套规则源。

查询路由技术说明：`docs/市场行情查询路由与全球时点规则_V1.0.md`。它只解释ETF系统内部查询路由实现，不是第五个规范域；唯一无网络路由实现为`scripts/market_quote_router.py`，单对象查询入口为`scripts/query_market_object.py`，查询时即时补采由`scripts/query_time_market_refresh.py`执行。

## 3. 场景读取路由

### 正式ETF分析

盘前、集合竞价、盘中、午间、盘后、风险许可、生命周期、金额、卖出和资本比较：

`本INDEX → system_consistency → MASTER → 当前必要账户／行情／decision context → 所属规范或研究证据`

盘中执行最小充分读取。需要当前行情时，按数据规范和`config/runtime_policy.json`判断当前场景，优先消费本次请求后的第一份合格正式行情；没有才触发一次查询时即时补采。同一用户请求不得重复创建第二条等价补采链。正式回复前最后复核一次当前正式行情，已完成的新行情必须纳入本次回复。

普通无新增成交的盘中券商截图可直接作为本次最新账户事实参与判断，不等待Dashboard持久化；真实成交、资金划转或其他会改变持仓／现金／生命周期的账户事件必须先闭环必要账户事实。

### 券商截图自动路由

用户仅上传券商持仓截图并`@GitHub`时，不询问用途。真实成交事件优先于时段路由；具体`interaction_scenario`、时间窗口和当前运行参数统一读取`config/runtime_policy.json`，不在INDEX维护第二份时间表。截图提交时间与市场行情时间必须分离。

### 系统维护、故障和生产变更

`本INDEX → 当前main → docs/生产变更与并发写入协议_V1.0.md → config/maintenance/production_mutation_protocol.json → 相关canonical owner／脚本／workflow → 组合验收`

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
- 查询时补采请求：`requests/live_snapshot/*.json`
- A股行情workflow：`.github/workflows/market-snapshot.yml`
- 海外盘前workflow：`.github/workflows/overseas-preopen-pulse.yml`
- 正式事实低层写入：`scripts/formal_file_mutation_gateway.py`

研究文件存在不等于正式转化。哪些研究允许进入执行及稳定使用边界读取MASTER第6.4；研究如何取得正式资格读取MASTER第8.1；动态研究数值读取`research_execution_summary.json`及对应证据文件。

## 5. 一致性与权威边界

INDEX只负责回答“去哪里读”，不得重新定义以下内容：正式指数全集、ETF监测全集、provider优先级、runtime参数、交易时段、通知阈值、生产变更准入规则、研究转化标准或交易权限。上述内容分别由所属规范和机器事实承载。

系统一致性检查必须验证正式文件入口、规范域入口、关键机器配置／状态、三层监测、研究登记、writer ownership、生产治理镜像、workflow链和E2E可用性。`PASS`表示已覆盖的生产结构一致；`WARNING`表示可解释的降级；`FAIL`表示硬冲突，不能把当前云端结构视为完整生产状态。

任何实现与所属唯一规范冲突时，应修复实现或规范镜像漂移；不得让INDEX、配置、脚本或历史聊天反向创造新的规则。README同样只承担导航和运行说明，不复制正式版本号、provider优先级或其他易变事实。
