# ETF反复做T与波段资本效率专项研究（Stage 1 修正版）

## 结论

本次研究已按新的 inventory/release/rebuy 定义重跑正式 11 只 ETF，但资格仍为 `FAIL / RESEARCH_ONLY`：预注册的 2% 偏离、5 日趋势保护、D 收盘信号、D+1 开盘执行、3 日持有、25% mobile，在 10/20/30bp 成本下没有形成跨年份稳定的相对 buy-and-hold 净 alpha。

核心修正是：此前实现把 mobile 建模成“75% ETF 核心仓 + 25% 初始闲置现金、下跌时择时买入”；本版本建模为“初始 100% ETF 暴露，其中 core 与 mobile 都已持有”，并用 `MOBILE_INVESTED → MOBILE_CASH → MOBILE_INVESTED` 状态机约束释放与回补。释放只出售已持有 mobile，回补只使用释放后的现金，不增仓、不杠杆；buy-and-hold 也从 100% ETF 暴露起步。

## 数据与方法

输入为 canonical `events/research/daily_features/*.json`，646 个交易日，正式 universe 精确 11 只；159326、159561 的样本分别为 479、564 个观测，其余正式对象为上市后真实样本。信号使用 D 收盘时可见的过去 20 日均值/偏离、5 日动量和历史波动，最早在 D+1 开盘成交；没有用 high/low 伪造日内先后。历史分钟、bid/ask、逐日制度标签、溢折价和可审计 intraday_path 不足，因此严格日内 T 不执行。

扫描为 10/20/30bp round-trip、1/1.5/2/2.5/3/4% 阈值、1/2/3/5/10 日持有期和 10/20/25/30/40/50% mobile。walk-forward 固定为 2024 calibration、2025 validation、2026 YTD holdout；默认 2%/3 日/25% 为预注册参数，holdout 不参与调参。beta-control 为 70% ETF+30%永久现金且零策略交易。所有结果写入 `research/backtests/etf_t_swing_stage1_validation.json`。

## 正式 11 只：20bp 修正结果

下表为 trend-filter core+mobile；alpha 是策略净收益减同一对象 buy-and-hold。

|代码|名称|观察数|B&H|core+mobile|alpha|交易/换手|right-tail|wrong-rebuy|
|---|---|---:|---:|---:|---:|---:|---:|---:|
|515880|通信ETF|646|298.22%|235.79%|-62.43pp|25/8.24|31.74%|7.21%|
|561980|半导体设备ETF|646|260.21%|212.84%|-47.37pp|20/6.02|15.74%|4.86%|
|159781|科创创业ETF|646|124.02%|103.03%|-20.98pp|20/6.27|18.21%|5.26%|
|588000|科创50ETF|646|98.42%|75.21%|-23.21pp|16/4.15|13.20%|4.31%|
|518880|黄金ETF|646|95.41%|74.86%|-20.55pp|15/4.34|8.05%|3.99%|
|513520|日经ETF|646|72.57%|64.72%|-7.84pp|19/6.37|10.11%|3.97%|
|159941|纳指ETF|646|82.11%|63.24%|-18.87pp|13/3.43|8.30%|5.48%|
|159326|电网设备ETF|479|69.89%|52.30%|-17.59pp|11/3.12|11.73%|5.89%|
|159561|德国ETF|564|38.91%|41.34%|+2.43pp|17/5.45|14.19%|3.20%|
|513180|恒生科技ETF|646|16.33%|17.65%|+1.32pp|20/6.67|5.69%|4.70%|
|159992|创新药ETF|646|4.05%|6.42%|+2.37pp|24/6.11|5.50%|3.58%|

按 20bp core+mobile 绝对净收益前三为 515880、561980、159781；按相对 B&H alpha 则为 159561、159992、513180，但仅为单样本窗口的轻微正值，不能称稳定 alpha。重点对象按绝对实现收益重排为 515880、561980、159781；按择时增量为 159561、159992、513180。588000 未成为最优。最不适合长期机械反复 T 的对象重新判为 515880：绝对收益高来自强 beta/趋势，但相对 B&H 损失最大、right-tail 损失也最大。

## 成本、mobile、周期与损失

10/20/30bp 下绝对净收益前三均为 515880、561980、159781；成本提高只会进一步降低结果。以 588000 为例，10/20/25/30/40/50% mobile 的 20bp 净收益为 89.13%/79.85%/75.21%/70.56%/61.28%/51.99%，相对 B&H 全部为负；20—30%是风险暴露折中而不是稳定 alpha 区间。full swing 更脆弱，beta-control 无策略交易，不能把其降 beta 结果冒充 alpha。

现有日线样本不支持日内 T；1/2/3/5/10 日仅作为隔日/短波段对照。偶尔的正 alpha 来自释放后回补的均值回归，但没有稳定覆盖交易费用、wrong-rebuy 和趋势上涨期间的 right-tail opportunity loss。right-tail 只在 release→rebuy 区间计量，wrong-rebuy 只在真实 rebuy 之后计量。应在真实突破、趋势加速、持续相对强势和承接改善时关闭/缩小卖出型 T，在弱势未确认修复时延迟回补。

## 历史15分钟能力复核

仓库确实保留历史分时能力的治理与研究线索：`ashare_ancestral_capital_evidence_formal_conversion.json` 记录 `SINA_15M_RESEARCH_ONLY_NOT_PRODUCTION_PROVIDER`、69 个稳健性交易日和 10/11 对象覆盖；但该结果只引用了 Actions artifact，当前 checkout 没有对应的 Sina 15分钟原始行、缓存、脚本或 artifact id，无法在本轮重新取得并审计。当时的生产/研究边界明确禁止把 Sina 升格为生产 provider。

当前可复用的实际链是 `scripts/build_minute_path_features.py` / `scripts/minute_source_capability_poc.py` 的腾讯1分钟路径和 `scripts/akshare_capability_poc.py` 的 AkShare/Eastmoney research PoC；前者是当前日内结构证据，后者需要安装 AkShare且只验证当前日期1分钟路径。本环境实测未安装 AkShare，因此没有伪造 69 日15分钟回测，也没有把当前离散/1分钟快照改写成历史15分钟样本。故本报告只完成 Layer 1 日线研究；Layer 2 历史15分钟回测仍为 `INCOMPLETE_DATA_LIMITATION`，没有成本后日内 alpha 结论。

分年 relative-to-buy-and-hold alpha 已写入 JSON。以 588000 为例，2024/2025/2026 YTD 约为 -2.0pp/-11.0pp/-3.0pp，三年均不支持正向时间外增量。

## 假设、制度与外部候选

H1（588000 是最优反复波段工具）=`REJECTED`；H2（561980 的高振幅/趋势使机械 T 劣于 588000）=`INSUFFICIENT_EVIDENCE`；H3（159781 优于 561980）=`REJECTED`。跨境/QDII、商品和境内 ETF 的 T+0/T+1、时差、NAV 偏离、额度和开盘停牌没有逐日 PIT 标签，不能声称 T+0 提高了可实现净收益。

仓库可审计输入之外仅识别到 159687 一个候选，24 个观测（2026-07-13 至 2026-08-13），深度结果为 `INSUFFICIENT_EVIDENCE`。状态为 `EXTERNAL_MARKET_SCREEN_INCOMPLETE_DATA_LIMITATION`，不是全市场筛选完成；不能声称没有新 ETF 击败现有最佳，也不建议把 159687 加入观察池。正式 universe、Dashboard、MASTER 均未修改。

## MASTER 8.1、验收与残余风险

`data_quality`、PIT 日线逻辑和可复现性满足研究级要求；159687 样本不足，`logic_stability`、`incremental_value`、`execution_translatability`、`benefit/cost` 和 `risk_non_increase` 未满足正式转化门槛。因此 MASTER 8.1=`RESEARCH_ONLY`，不生成 conversion review，不增加交易权限。残余 blocker 为全市场历史面板、分钟/盘口与 T+0 制度数据缺失，以及 Windows 本地 OpenSSL/SM4 环境门可能失败；Ubuntu canonical gate 必须以 PR 最新 head 的真实 CI 为准。

复现：`python research/backtests/analyze_etf_t_swing.py`。测试覆盖状态转移、初始 100% 暴露、无杠杆、资本守恒、实际 release/rebuy leg 成本、PIT/no-lookahead、right-tail/wrong-rebuy、beta-control、正式 universe 不变、外部候选不改生产名单和日内数据边界。
