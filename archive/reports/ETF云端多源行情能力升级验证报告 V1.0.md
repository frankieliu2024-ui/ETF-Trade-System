# ETF云端多源行情能力升级验证报告 V1.0

验证日期：2026-08-23。范围仅为数据能力，不修改交易规则，不生成交易建议或买卖动作。

## 1. 结论

多源行情架构已建立并完成首轮真实对象探针。Yahoo 公共 chart API 为海外对象提供了可用补充：SOX、SOXX、SOXQ、NDX、SPX、KOSPI、KOSDAQ、N225 通过 OHLCV 完整性和 OHLC 逻辑检查；三星电子、SK海力士、VIX、TWII、黄金、DXY 获取成功但降级；HSTECH 当前符号返回 HTTP 404。获取成功不等于自动采用，生产接入仍需质量门控和来源条款审查。

## 2. SOX与韩国半导体

SOX、SOXX、SOXQ 已由 Yahoo chart API 取得 1255 行 5 年日线，最新有效日期为 2026-08-21，OHLCV 完整且无逻辑异常。三星电子 `005930.KS`、SK海力士 `000660.KS` 可获取，但分别有 1 条缺失/1 条 OHLC 异常、1 条缺失/3 条 OHLC 异常，因此暂为 DEGRADED，不直接进入无条件正式监测。KOSPI、KOSDAQ 直接指数通过；HSTECH 直接符号失败，513180 继续作为 ETF 代理。

## 3. 美国和亚洲指数

NDX、SPX、N225 通过；KOSPI、KOSDAQ 通过；VIX 与 TWII 获取成功但字段缺失，标记降级；HSTECH 失败。与上一版相比，海外指数覆盖从“未核验”提升到“多对象可获取”，但仍不是全部对象达到 PASS。

## 4. 商品与美元

黄金 `GC=F`、DXY `DX-Y.NYB` 均可取得历史响应，最新有效日期为 2026-08-21；因存在缺失必需字段行，均标记 DEGRADED。旧的本地历史文件仍保留，不被自动覆盖。

## 5. 开源库评估

- yfinance：Apache-2.0，pip 可解析到 1.6.0；适合作为海外探针候选，但实际数据来自 Yahoo 公开接口，须遵守 Yahoo 数据使用条款。
- AKShare：MIT，pip 可解析到 1.18.94，适合作为候选第二源；其接口来源多、逐接口稳定性和许可需要单独审计。
- pandas-datareader：BSD-3-Clause，当前项目重点偏宏观、政策和因子数据，不适合作为本轮海外逐对象主源。
- investpy：MIT，但最新稳定发布停留在 2022-01-24，不作为首选。

## 6. 已落地文件

- `scripts/multi_source_market.py`：只读 Yahoo chart 标准化与质量检查适配器。
- `provider_priority.json`：质量优先的多源优先级，不代表自动采用。
- `market_data_provider_matrix.md`：对象×源能力矩阵。
- `data/market/audit/multi_source_probe_2026-08-23.json`：本次探针摘要。
- `market_monitor_config.json`：增加 provider 及 ETF 角色区分；SOXQ、513520、513180、518880 明确为 ETF 交易目标，可兼作 INDEX_PROXY。

## 7. 建议正式接入对象

第一批建议进入“待正式接入、仍需生产质量门控”的对象：SOX、SOXX、SOXQ、NDX、SPX、KOSPI、KOSDAQ、N225。三星电子、SK海力士、VIX、TWII、黄金、DXY 保持 DEGRADED；HSTECH 继续等待可靠直接源，不能用代理伪装成直接指数。

## 8. 风险和保护

Yahoo 公开接口存在限流、符号变化、历史修订、字段缺失和使用条款风险；海外市场时区不同，未调整价格与复权价格不可混用。当前适配器不写 MASTER、Dashboard、CURRENT、账户事实，也不生成交易金额或交易动作。正式接入前仍需 GitHub Actions 环境重跑、失败重试、缓存/快照幂等和来源许可确认。
