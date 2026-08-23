# ETF云端多源行情能力矩阵 V1.0

验证日期：2026-08-23；Yahoo 公共 chart API 探针使用 5 年日线、`auto_adjust=false`。本矩阵记录获取与质量事实，不表示自动采用，也不产生交易判断。

## 1. 对象 × 数据源

| 对象 | 源 | 成功 | 历史 | 质量 | 状态 |
|---|---|---:|---|---|---|
| SOX | Yahoo chart `^SOX` | 是 | 1255行，至2026-08-21 | OHLC完整、0异常；EDT | PASS |
| SOXX | Yahoo chart `SOXX` | 是 | 1255行，至2026-08-21 | OHLC完整、0异常；EDT | PASS |
| SOXQ | Yahoo chart `SOXQ` | 是 | 1255行，至2026-08-21 | OHLC完整、0异常；EDT | PASS |
| 三星电子 | Yahoo chart `005930.KS` | 是 | 1220行，至2026-08-21 | 1缺失行、1条OHLC异常；KST | DEGRADED |
| SK海力士 | Yahoo chart `000660.KS` | 是 | 1220行，至2026-08-21 | 1缺失行、3条OHLC异常；KST | DEGRADED |
| NDX | Yahoo chart `^NDX` | 是 | 1255行，至2026-08-21 | OHLC完整、0异常；EDT | PASS |
| SPX | Yahoo chart `^GSPC` | 是 | 1255行，至2026-08-21 | OHLC完整、0异常；EDT | PASS |
| VIX | Yahoo chart `^VIX` | 是 | 1305行，最新有效至2026-08-21 | 49缺失必需字段行；CDT | DEGRADED |
| KOSPI | Yahoo chart `^KS11` | 是 | 1220行，至2026-08-21 | OHLC完整、0异常；KST | PASS |
| KOSDAQ | Yahoo chart `^KQ11` | 是 | 1220行，至2026-08-21 | OHLC完整、0异常；KST | PASS |
| N225 | Yahoo chart `^N225` | 是 | 1223行，至2026-08-21 | OHLC完整、0异常；JST | PASS |
| TWII | Yahoo chart `^TWII` | 是 | 1215行，最新有效至2026-08-21 | 1缺失必需字段行；CST | DEGRADED |
| HSTECH | Yahoo chart `^HSTECH` | 否 | 无 | HTTP 404 | FAILED |
| 黄金 | Yahoo chart `GC=F` | 是 | 1521原始行，最新有效至2026-08-21 | 264缺失必需字段行；EDT | DEGRADED |
| DXY | Yahoo chart `DX-Y.NYB` | 是 | 1521原始行，最新有效至2026-08-21 | 264缺失必需字段行；EDT | DEGRADED |

同花顺仍作为 A 股 ETF 主源；本表不将 Yahoo 成功结果自动替换进生产快照。所有对象必须经过重复、缺失、OHLC、时区及复权口径审计后才可进入正式监测。

## 2. Python / 开源库评估

| 库/项目 | 项目地址 | License | 更新/安装观察 | Key | 结论 |
|---|---|---|---|---|---|
| yfinance | `ranaroussi/yfinance` | Apache-2.0；实际 Yahoo 数据仍受 Yahoo 条款约束 | pip 可解析到 1.6.0；GitHub活跃 | 不需要 | 首选海外探针；已用同一 Yahoo chart 后端完成对象验证 |
| AKShare | `akfamily/akshare` | MIT | pip 可解析到 1.18.94；GitHub仍有发布活动 | 通常不需要，依接口而定 | 候选第二源；接口变动和来源许可需逐接口审计 |
| pandas-datareader | `pydata/pandas-datareader` | BSD-3-Clause | 0.11.1；当前重点偏宏观、政策、因子数据 | 取决于源 | 不适合作为本轮海外逐对象主源 |
| investpy | `alvarobartt/investpy` | MIT | PyPI 1.0.8 / GitHub最新稳定版 2022-01-24；上游新鲜度不足 | 不需要 | 不作为首选；仅可做历史研究候选 |

## 3. 角色边界

- SOX、NDX、SPX、VIX、KOSPI、KOSDAQ、N225、TWII、DXY：指数或价格指标。
- SOXX、SOXQ、513520、513180、518880：`ETF_TRADING_TARGET`；在直接指数缺失时可标记 `INDEX_PROXY`，不得与直接指数混称。
- 三星电子、SK海力士：海外个股产业链观察对象，不属于 ETF 交易目标。
- HSTECH：当前直接 Yahoo 符号失败；513180 只能作为 ETF 代理。
