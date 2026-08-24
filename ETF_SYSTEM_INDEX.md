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

任何涉及三层监测对象、provider优先级、ETF运行全集、交易日历、脉冲参数、跨市场时点、状态文件、脚本、workflow或代码提交的更新，都必须同步复核该规范并执行系统一致性检查。

## 三层市场监测结构

第一层：指数。正式固定复核上证指数、创业板指、NDX、SOX、N225、KOSPI、TWII、HSTECH；其他指数或商品按具体假设条件调用。

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

`PASS`表示结构一致；`WARNING`表示账户事实缺失等正常状态；`FAIL`表示硬冲突，必须先修复。`.github/workflows/system-consistency.yml` 在相关维护推送main后自动运行并持久化结果；生产workflow在采集前再次预检。

## 运行读取路径

- 基础数据规范：`ETF与市场监测数据接口使用规范.md`
- A股官方交易日历：`config/market/a_share_trading_calendar_2026.json`
- 状态：`data/state/CURRENT.json`、`data/state/account_fact.json`、`data/state/runtime_health.json`
- 系统一致性：`data/state/system_consistency.json`、`scripts/check_system_consistency.py`
- ETF机器运行全集：`config/market/etf_monitor_universe.json`
- 动态个股层：`data/state/asset_roles.json`、`data/state/stock_context.json`、`data/state/stock_market_context.json`
- provider优先级：`config/market/provider_priority.json`
- 实时相邻变化：`data/state/market_delta.json`
- 正式海外/亚洲指数：`data/state/overseas_context.json`
- 美股扩展时段：`data/state/us_extended_hours_context.json`、`scripts/build_us_extended_hours_context.py`
- 三层监测配置：`config/market/market_monitor_config.json`
- 运行策略：`config/runtime_policy.json`
- 查询上下文：`data/state/query_context.json`、`data/state/decision_context.json`
- A股/ETF workflow：`.github/workflows/market-snapshot.yml`
- 海外盘前 workflow：`.github/workflows/overseas-preopen-pulse.yml`
- 一致性 workflow：`.github/workflows/system-consistency.yml`
- 运行脚本：`scripts/`
- 行情快照：`data/market/snapshots/`
- 审计：`data/market/audit/`

## 盘前、盘中与数据时点原则

市场监测不是9:30才启动：

- 北京时间07:00起：读取上一美股交易日盘后尾段，默认使用QQQ/SOXX扩展时段代理；
- 北京时间08:00：日经225、KOSPI常规交易开始，亚洲盘前独立脉冲继续；
- 北京时间09:00：台湾加权进入常规交易，香港进入盘前阶段；
- 北京时间09:15：A股进入开盘集合竞价，09:15/09:20/09:25为集合竞价事件脉冲；
- 北京时间09:30以后：A股连续竞价按10分钟级目标脉冲运行；
- A股15:00收盘后允许收盘宽限补采。

集合竞价数据必须标记 `OPENING_CALL_AUCTION`，不得和连续竞价价格/承接语义混用。只有本次workflow真实写入新PASS快照后，才允许刷新依赖A股当前行情的下游context。

### 美国时间关系

美国信息必须拆成三类：

1. `REGULAR`：NDX/SOX等正式现金盘结构；A股上午通常读取上一美股交易时段。
2. `POST_MARKET`：上一美股交易日盘后。Nasdaq盘后为美东16:00—20:00；夏令时大致对应北京时间04:00—08:00，冬令时大致05:00—09:00，因此可能在A股开盘前提供新的价格发现。
3. `PRE_MARKET`：下一美股交易日盘前。Nasdaq盘前为美东04:00—09:30，通常对应北京时间16:00—21:30（夏令时）或17:00—22:30（冬令时），主要形成下一A股交易日的前置信号，而不是当天A股上午的同步行情。

QQQ/SOXX作为扩展时段方向代理；INTC、NVDA、AMD等产业链个股只在具体ETF/行业假设需要时动态调用。扩展时段必须标注 `PRE_MARKET/POST_MARKET`，不得把“英特尔盘前+5%”写成“SOX上涨5%”。原则是：不高估海外行情的时间领先，也不低估其时间领先。

任何正式行情分析必须强制显示数据时点，并统一优先使用北京时间：

```text
【数据时点（北京时间）】YYYY-MM-DD HH:MM:SS
```

A股使用 `captured_at_beijing`；海外/亚洲对象使用 `latest.as_of_beijing`；美股扩展时段同时使用 `latest.as_of_beijing` 与 `market_phase_of_latest`。若多个市场时点明显不同，分别标注，不能用一个总时间掩盖延迟或错位。

FRESH/DEGRADED/STALE必须按真实数据时点重新计算，不能按cron计划时刻判断。美国现金指数、盘后、盘前及亚洲各市场必须按各自实际阶段解释。

## 账户事实与动态个股原则

`account_fact.status == VALID` 仍必须满足当日market_date一致。旧日账户事实不得自动沿用。账户事实有效后，非ETF持仓结合 `asset_roles.json` 生成打新底仓监测；未知角色只请求一次确认。

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
2. 当前状态：一致性、数据规范、交易日历、CURRENT、runtime health、ETF全集、`overseas_context.json`、`us_extended_hours_context.json`、动态个股层，再读Dashboard。
3. 盘中查询：必须检查北京时间数据时点和新鲜度；海外对象逐一读取as_of与market phase；涉及美股科技/半导体传导时，同时区分上一现金盘、盘后及盘前。
4. 盘后维护：读取账户事实、review context及四文件；维护结束后再次执行一致性检查。

## 写入边界

自动程序可以生成行情、状态、审计、运行健康、一致性结果和上下文；不得自动写MASTER、不得自动下单。数据规范可在明确的数据架构或运行边界维护时更新，但每次更新必须通过系统一致性检查。
