# ETF云端市场监测数据能力矩阵 V1.1

维护基准日：2026-08-23。本文只记录当前生产数据能力、正式监测职责和时间口径，不记录交易判断。历史V1.0探针结论保留在归档报告，不再作为当前生产配置。

| 对象 | 正式职责 | 主数据源 | 备用/代理 | 当前生产边界 |
|---|---|---|---|---|
| 上证指数（000001.SH） | 正式指数层 | hithink-finance | 无 | ACTIVE；A股本地时点 |
| 创业板指（399006.SZ） | 正式指数层 | hithink-finance | 无 | ACTIVE；A股本地时点 |
| 纳斯达克100指数（NDX） | 正式指数层 | Yahoo chart API | 无 | ACTIVE；A股交易时段通常是上一美股交易时段参考 |
| 费城半导体指数（SOX） | 正式指数层 | Yahoo chart API | SOXQ | ACTIVE；直接指数优先，SOXQ仅备用代理 |
| 日经225指数（N225） | 正式指数层 | Yahoo chart API | 日经ETF（513520） | ACTIVE；按日本市场同日盘中/已收盘时点解释 |
| 韩国综合指数（KOSPI） | 正式指数层 | Yahoo chart API | 无 | ACTIVE；按韩国市场同日盘中/已收盘时点解释 |
| 台湾加权指数（TWII） | 正式指数层 | Yahoo chart API | 无 | ACTIVE；质量不足时DEGRADED但不静默遗漏 |
| 恒生科技指数（HSTECH） | 正式指数层 | hithink-finance HS2083优先 | Yahoo `^HSTECH` / 恒生科技ETF（513180）代理 | ACTIVE_WITH_FALLBACK；A股15:00不等于恒生科技当日收盘 |
| 标普500指数（SPX） | 保留适配能力 | Yahoo chart API | 无 | 非默认监测 |
| VIX | 保留适配能力 | Yahoo chart API | 无 | 非默认监测 |
| KOSDAQ | 条件辅助 | Yahoo chart API | 无 | 非默认监测 |
| 黄金上游 | 黄金ETF条件背景 | Yahoo/其他可靠源 | 黄金ETF（518880） | 条件调用；质量门控 |
| DXY | 条件辅助 | Yahoo/其他可靠源 | 无 | 条件调用；质量门控 |

## 跨市场时间字段

生产 `data/state/overseas_context.json` 对正式海外与亚洲指数至少保存并解释：

- `market_timezone`：目标市场本地时区；
- `latest.as_of_local`：该价格/Bar的目标市场本地时点；
- `market_phase_at_generation`：生成上下文时目标市场处于盘前、盘中、休市或已收盘；
- `time_relation_to_a_share`：相对当前A股决策时点是上一交易时段、同日盘中还是同日已收盘。

不同市场数据只有完成上述时点对齐后才能讨论共振或背离。日期相同不代表同步；美国现金指数不得在A股白天被描述为“当前同步盘中”。

## 数据质量原则

1. 正式监测职责与数据源可用性分开管理：接口失败可以使对象DEGRADED/FAILED，但不能自动把对象从正式指数层删除。
2. 直接指数优先；ETF代理只在直接指数不可用时辅助，不得将代理价格写成指数本身。
3. 单一海外对象失败不阻断八ETF、上证指数和创业板指的A股核心行情采集。
4. FAILED对象不得用旧值冒充当前状态；STALE历史值只作历史/背景信息。
5. 生产配置以 `config/market/market_monitor_config.json`、`config/market/provider_priority.json` 和 `ETF规则_MASTER.md` 的现行边界为准。
