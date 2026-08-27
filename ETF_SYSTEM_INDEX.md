# ETF-Trade-System 统一读取入口

本文件是仓库结构入口，不是交易规则。GitHub `main` 是云端唯一主版本；ChatGPT、Codex 和 GitHub Actions 均从本索引解析正式路径。

## 四个正式文件

| 职责 | 唯一路径 | 说明 |
|---|---|---|
| 规则 | `ETF规则_MASTER.md` | 交易规则唯一来源；不得自动修改 |
| 当前状态 | `ETF当前状态_DASHBOARD.md` | 当前状态和账户事实入口；规则不在此定义 |
| 经验复盘 | `ETF交易复盘与经验库_2026.md` | CASE、OBS、复盘历史 |
| 行情事实 | `ETF市场行情档案_2026.md` | 行情、成交、来源和质量事实 |

历史聊天不属于正式来源。发生冲突时遵循 MASTER > Dashboard > 当前行情与成交 > 经验库 > 行情档案 > 历史聊天。

## 一级目录基础数据规范

`ETF与市场监测数据接口使用规范.md` 是数据与云端运行的基础规范，负责三层监测结构、多数据源、盘前/盘中脉冲、新鲜度、交易日历、强制数据时点、质量验收、跨市场时间对齐、故障降级和维护一致性。它不是第五个交易规则文件，不产生风险许可、Trial／Confirm、金额、卖出或其他交易权限。

补充查询技术说明：`docs/市场行情查询路由与全球时点规则_V1.0.md` 定义用户查询时的市场、时区、阶段和数据选择说明；唯一实现入口为 `scripts/market_quote_router.py`，配置为 `config/market/market_quote_router.json`；查询时 provider 补采统一由 `scripts/query_time_market_refresh.py` 执行，成功结果先直返本次查询，再异步持久化。它不是交易规则，也不替代上述正式数据规范。 单对象查询统一入口为 `scripts/query_market_object.py`：正式监测对象返回 `SYSTEM_MONITORED`，用户明确指定但未纳入监测池的对象返回 `USER_REQUESTED`；扩展查询结果只写入现有 `query_context.json` 查询区域，不改变机器监测全集。

任何涉及三层监测对象、provider优先级、ETF运行全集、交易日历、脉冲参数、跨市场时点、状态文件、脚本、workflow或代码提交的更新，都必须同步复核该规范并执行系统一致性检查。

## 三层市场监测结构

第一层：指数。正式固定复核上证指数、创业板指、科创50指数、NDX、SOX、N225、KOSPI、TWII、HSTECH；其他指数或商品按具体假设条件调用。

第二层：ETF。日常统一为“持仓ETF + 观察ETF”。两类均持续获取行情并参加机会扫描、生命周期管理和统一资本比较；机器采集唯一运行清单为 `config/market/etf_monitor_universe.json`，不写死持仓/观察身份。Dashboard中的ETF全集必须与机器清单一致。

第三层：个股。默认个股监测由当日账户事实动态生成；已确认 `IPO_BASE_STOCK` 进入打新底仓监测，未知角色标记 `UNCLASSIFIED_STOCK`。产业链观察个股按ETF/行业假设临时发现，可覆盖A股、美股、港股、韩股、台股、日股等，不形成永久名单。

## 系统一致性门禁

一致性检查是相关更新维护后的基础操作，不只是文本口径检查。至少覆盖：

- 四个正式文件与数据规范；
- 三层监测对象、provider、ETF全集、交易日历、runtime参数；
- workflow → session gate → provider → snapshot → CURRENT/runtime_health → downstream context → commit 的运行链；
- 关键文件是否被Git跟踪、当前HEAD/GITHUB_SHA、工作树是否存在未提交关键修改；
- 强制北京时间数据字段与跨市场阶段字段；
- 美股现金盘、POST_MARKET、PRE_MARKET是否被正确分离；
- `system_consistency.json` 自动验收结果和真实生产脉冲状态。

`PASS`表示结构一致；`WARNING`表示账户事实缺失等正常状态；`FAIL`表示硬冲突，必须先修复。`.github/workflows/system-consistency.yml` 在相关维护推送main后自动运行并持久化结果；高频行情workflow不再在provider采集前重复执行完整一致性检查，以避免阻塞行情主链。

## 运行读取路径

- 基础数据规范：`ETF与市场监测数据接口使用规范.md`
- A股官方交易日历：`config/market/a_share_trading_calendar_2026.json`
- 状态：`data/state/CURRENT.json`、`data/state/account_fact.json`、`data/state/runtime_health.json`
- 系统一致性：`data/state/system_consistency.json`、`scripts/check_system_consistency.py`
- ETF机器运行全集：`config/market/etf_monitor_universe.json`
- 动态个股层：`data/state/asset_roles.json`、`data/state/stock_context.json`、`data/state/stock_market_context.json`
- provider优先级：`config/market/provider_priority.json`
- 实时相邻变化：`data/state/market_delta.json`
- 日内路径特征：`data/state/intraday_path_features.json`、`scripts/build_intraday_path_features.py`；由当日连续有效快照重建离散分时路径，输出低点修复、距高低位、近期斜率、成交增量、相对指数强弱和描述性结构候选；不得直接生成交易动作
- 正式海外/亚洲指数：`data/state/overseas_context.json`
- 美股扩展时段：`data/state/us_extended_hours_context.json`、`scripts/build_us_extended_hours_context.py`
- 三层监测配置：`config/market/market_monitor_config.json`
- 运行策略与全天截图路由：`config/runtime_policy.json`
- 查询上下文：`data/state/query_context.json`、`data/state/decision_context.json`；其中 `decision_context.json` 是ChatGPT盘中快速读取的聚合决策上下文，并内嵌最新日内路径特征，不另建平行decision bundle
- `data/state/research_execution_summary.json`：研究结论归纳后的只读执行证据桥，覆盖ETF与当前打新底仓；可改变正式判断但不自动交易。
- 收盘复盘上下文：`data/state/review_context.json`、`post_market_review/post_market_review_event.json`
- 查询时即时补采与可选状态同步请求：`requests/live_snapshot/*.json`
- 账户/Dashboard/成交/正式复盘异步同步处理：`scripts/process_state_sync_request.py`
- A股/ETF workflow：`.github/workflows/market-snapshot.yml`
- A股开盘前海外 workflow：`.github/workflows/overseas-preopen-pulse.yml`
- 美股盘前/扩展时段 workflow：`.github/workflows/us-extended-hours-pulse.yml`
- 一致性 workflow：`.github/workflows/system-consistency.yml`
- 运行脚本：`scripts/`
- 行情快照：`data/market/snapshots/`
- 审计：`data/market/audit/`

## 全天券商截图自动路由

券商截图按北京时间自动进入对应场景；真实成交事件优先于时段路由。默认路由以 `config/runtime_policy.json` 为机器来源：

|北京时间|场景|默认处理|
|-|-|-|
|00:00–06:59|`OFF_HOURS_ACCOUNT_UPDATE`|账户事实差异更新；仅在用户明确要求时做前瞻分析|
|07:00–09:14|`PRE_MARKET`|正式盘前检查；A股使用上一交易日收盘，海外/亚洲按各自实际阶段即时补采|
|09:15–09:29|`OPENING_CALL_AUCTION`|集合竞价检查；不得按连续竞价成交承接解释|
|09:30–11:30|`INTRADAY`|上午盘中决策；查询时即时补采优先|
|11:31–12:59|`MIDDAY_REVIEW`|上午半日复盘＋下午计划；A股使用11:30上午收盘，其他市场按各自最新时点|
|13:00–14:59|`INTRADAY`|下午盘中决策；查询时即时补采优先|
|15:00–23:59|`POST_CLOSE_REVIEW`|当天首次最终账户截图默认触发正式收盘复盘；A股使用15:00正式收盘，同日重复截图仅做差异更新|

截图提交时间和市场行情时间必须分离。比如18:00上传当天收盘账户截图，账户确认时点可以是18:00，但A股复盘行情必须使用当天15:00正式收盘数据；恒生科技及其他海外/亚洲市场仍按各自实际 `as_of_beijing` 与 `market_phase` 解释。

任何时段如截图或用户确认存在实际成交，先处理成交事件、刷新持仓/现金/生命周期，再进入该时段对应分析。当天正式收盘复盘以 `market_date` 为幂等键：已经完成且账户/成交事实没有变化时，后续重复截图不重复制造CASE或复盘记录；如出现此前未记录成交或账户变化，则修订当天正式状态。

## 盘中快速回复与异步维护

用户回复优先于文档维护。ChatGPT收到券商截图或正式盘中检查请求时，固定按以下顺序执行：

1. 先从截图确认当次账户、持仓、现金和成交事实，并确定 `interaction_scenario`；
2. 需要当前市场行情时立即通过 `requests/live_snapshot/*.json` 触发查询时补采，查询时行情优先于最近生产快照；午间和收盘后则使用对应已结束A股时段的正式快照，不把请求时间冒充行情时间；
3. 新核心 `CURRENT` 在短等待预算内可用则使用；若即时补采尚未完成而最近有效快照仍为FRESH，则立即回退最近FRESH快照并明确标注真实时点，不为等待文档维护延迟正式回复；
4. 每次有效快照后，`build_state_context` 同步刷新 `intraday_path_features.json`，并把最新特征嵌入 `decision_context.json`；ChatGPT可直接读取路径特征判断V型修复、急跌、突然拉升、高位震荡等结构候选，无需常规依赖用户上传分时图。ETF份额、融资融券等日频研究在A股连续交易阶段只读取最近有效缓存，不重复发起低频外网请求；真正刷新放在收盘后的低频阶段；
5. 日内路径特征属于行情事实层。`RAPID_RISE_CANDIDATE`、`SHARP_DROP_CANDIDATE`、`V_RECOVERY_CANDIDATE`、`HIGH_ZONE_CONSOLIDATION_CANDIDATE` 仅为描述性候选标签，必须同时读取采样覆盖和最大间隔；离散脉冲不能恢复两次采样之间全部分钟级路径，标签不得绕过MASTER生成风险许可、机会、金额或买卖动作；
6. 同一查询请求可携带 `account_fact`、`formal_decision`、`trade_event`、`interaction_scenario`、`formal_review`。13个A股核心对象与 `CURRENT` 先发布，账户/Dashboard及其他下游维护随后继续；
7. `formal_decision` 和 `formal_review` 只允许写入ChatGPT已经形成的正式结论，自动程序不得自行推导风险许可、生命周期、金额、卖出动作或复盘经验；
8. 普通无成交盘中截图更新账户事实和Dashboard云端实时状态区块，不机械改写MASTER、经验库或行情档案；
9. 若存在用户确认或券商事实确认的真实成交，除更新账户事实和Dashboard外，同时生成 `events/trades/` 成交事件，在行情档案登记客观成交，并在经验库生成待复盘CASE入口；
10. `POST_CLOSE_REVIEW` 在正式收盘数据和账户事实满足门禁后，可按ChatGPT已经形成的正式复盘结论幂等维护Dashboard、行情档案和经验库；MASTER仅检查是否需要正式规则维护，默认不修改；
11. `ETF规则_MASTER.md` 永不由异步维护链自动修改，系统不自动下单。

Dashboard中的 `<!-- AUTO_STATE_SYNC_START -->` 至 `<!-- AUTO_STATE_SYNC_END -->` 为机器管理实时区块，优先展示最新已确认账户事实和最近一次ChatGPT正式决策/复盘；其后的历史人工维护内容保留用于追溯，不得用旧历史区块覆盖机器管理区块中的更新事实。

## 盘前、盘中与数据时点原则

市场监测不是9:30才启动：

- 北京时间07:00起：读取上一美股交易日盘后尾段，默认使用QQQ/SOXX扩展时段代理；
- 北京时间08:00：日经225、KOSPI常规交易开始，亚洲盘前独立脉冲继续；
- 北京时间09:00：台湾加权进入常规交易，香港进入盘前阶段；
- 北京时间09:15：A股进入开盘集合竞价，09:15/09:20/09:25为集合竞价事件脉冲；
- 北京时间09:30以后：A股连续竞价按10分钟级目标脉冲运行；
- 11:30–13:00：A股午休，A股正式分析使用11:30上午收盘；海外/亚洲对象按各自市场实际阶段继续刷新；
- A股15:00收盘后允许收盘宽限补采；`POST_CLOSE_GRACE` 属于收盘数据补采阶段，可用于确认当天正式收盘复盘已具备行情门禁；
- 北京时间16:00—22:30：独立美股扩展时段workflow覆盖夏令时/冬令时盘前窗口，并按 `America/New_York` 实际阶段自动标记PRE_MARKET或REGULAR。

集合竞价数据必须标记 `OPENING_CALL_AUCTION`，不得和连续竞价价格/承接语义混用。只有本次workflow真实写入新PASS快照后，才允许刷新依赖A股当前行情的下游context。

### 美国时间关系

美国信息必须拆成三类：

1. `REGULAR`：NDX/SOX等正式现金盘结构；A股上午通常读取上一美股交易时段。
2. `POST_MARKET`：上一美股交易日盘后。Nasdaq盘后为美东16:00—20:00；夏令时大致对应北京时间04:00—08:00，冬令时大致05:00—09:00，因此可能在A股开盘前提供新的价格发现。
3. `PRE_MARKET`：下一美股交易日盘前。Nasdaq盘前为美东04:00—09:30，通常对应北京时间16:00—21:30（夏令时）或17:00—22:30（冬令时），主要形成下一A股交易日的前置信号，而不是当天A股上午的同步行情。

QQQ/SOXX作为扩展时段方向代理；INTC、NVDA、AMD等产业链个股只在具体ETF/行业假设需要时动态调用。扩展时段必须标注 `PRE_MARKET/POST_MARKET`，不得把“英特尔盘前+5%”写成“SOX上涨5%”。原则是：不高估海外时间领先，也不低估海外时间领先。

任何正式行情分析必须强制显示数据时点，并统一优先使用北京时间：

```text
【数据时点（北京时间）】YYYY-MM-DD HH:MM:SS
```

A股使用 `captured_at_beijing`；海外/亚洲对象使用 `latest.as_of_beijing`；美股扩展时段同时使用 `latest.as_of_beijing` 与 `market_phase_of_latest`。若多个市场时点明显不同，分别标注，不能用一个总时点掩盖延迟或错位。

FRESH/DEGRADED/STALE必须按真实数据时点重新计算，不能按cron计划时刻判断。美国现金指数、盘后、盘前及亚洲各市场必须按各自实际阶段解释。

## 账户事实与动态个股原则

账户事实采用事件驱动有效机制。最近一次已验证 `account_fact.status == VALID` 的账户事实，在没有用户确认或券商事实确认的成交、资金划转、公司行为或其他账户变更事件时可以继续沿用；一旦出现账户变更事件，必须先刷新 `account_fact.json` 再进行账户敏感决策。用户新提供的券商截图优先更新为最新账户事实。账户事实有效后，非ETF持仓结合 `asset_roles.json` 生成打新底仓监测；未知角色只请求一次确认。

## 故障退化

- 系统一致性FAIL：停止把当前结构当作完整生产状态；
- 官方休市或窗口外：跳过A股生产采集；
- runner未写入新快照：下游A股依赖context跳过；
- Hithink/Yahoo单对象失败：局部FAILED/DEGRADED，不静默删除监测职责；
- 美股扩展时段无有效bar：不得用上一正式现金盘冒充盘前/盘后；
- 数据时点陈旧：降级，不冒充当前；
- Git推送冲突：rebase后重试，不能覆盖正式文件；
- 账户事实缺失：行情可继续，账户/资金/卖出正式判断受门禁。

## 读取场景

1. ChatGPT规则读取：本索引 → `system_consistency.json` → MASTER；涉及数据时同时读数据规范。
2. 当前状态：一致性、数据规范、交易日历、CURRENT、runtime health、ETF全集、`overseas_context.json`、`us_extended_hours_context.json`、动态个股层，再读Dashboard；盘中快速路径可先读取 `decision_context.json`，其中已包含最新日内路径特征，规则版本/hash或具体条款需要核实时再读取MASTER全文。
3. 盘前/盘中查询：按全天截图路由执行；需要当前行情时先执行查询时即时补采，失败、超时或尚未完成时才回退最近有效快照，并检查北京时间数据时点和新鲜度；海外对象逐一读取as_of与market phase。
4. 午间复盘：A股使用11:30上午收盘，其他市场按各自最新有效时点；形成下午第一观察条件和资本用途判断。
5. 盘后维护：15:00后当天首次最终账户截图默认进入正式收盘复盘；读取账户事实、review context及四文件，按需维护Dashboard、行情档案、经验库，MASTER默认不改；维护结束后再次执行一致性检查。

## 写入边界

自动程序可以生成行情、状态、审计、运行健康、一致性结果和上下文；可以在已确认账户事实和ChatGPT已形成正式决议/复盘的边界内维护Dashboard机器管理区块及真实成交、正式复盘事实入口；不得自动写MASTER、不得自行扩大交易权限、不得自动下单。数据规范可在明确的数据架构或运行边界维护时更新，但每次更新必须通过系统一致性检查。

### 账户事实正式文件维护（V1.0）

- 入口：已确认的账户事实状态同步请求，经 `scripts/process_state_sync_request.py` 处理后调用 `scripts/sync_formal_files.py`。
- 范围：幂等更新 `ETF当前状态_DASHBOARD.md`、`ETF交易复盘与经验库_2026.md` 和 `ETF市场行情档案_2026.md` 的客观事实区块；手续费确认可增量关闭对应成交索引的待确认字段。
- 边界：`account_fact.json` 是事实源；模块不得写入 `ETF规则_MASTER.md`，不得生成交易权限、金额、买卖动作或新的生命周期；文档维护失败不得改变已确认账户事实和核心行情快照。
- 验收：`tests/test_market_formal_file_sync.py` 覆盖幂等、动作别名、千分位数量和不臆造费用。
