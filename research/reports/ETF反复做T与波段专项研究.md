# ETF反复做T／波段资本效率专项研究（Stage 1）

## 结论先行

本阶段结论为 `FAIL / RESEARCH_ONLY`：在当前可审计的 2024-01-02 至 2026-08-28 日线 PIT 面板中，预注册的“收盘 D 信号、开盘 D+1 执行、20 日偏离、5 日趋势过滤、3 日持有、25%机动仓”没有击败同一 ETF 的 buy-and-hold。严格日内 T 没有足够历史分钟覆盖，不能用日线 high/low 代替成交顺序，因此不作有效性结论。

20bp round-trip 下，现有 11 只 ETF 的 core+mobile 研究收益前三为：515880 通信 ETF 219.94%、561980 半导体设备 ETF 208.13%、159781 科创创业 ETF 98.78%；对应 buy-and-hold 分别为 288.76%、259.16%、120.91%。这说明累计收益主要来自持有的 beta／趋势暴露，机动交易反而产生相对损失，不能称为稳定 alpha。所有结果都必须结合回撤、卖飞和错误回补损失阅读。

## 数据审计与方法

唯一复用的历史研究面板是 `events/research/daily_features/<market_date>.json`：646 个交易日、现有正式 11 只 ETF，字段包括 OHLC、volume、amount 和历史形成的研究特征。159326 只有 479 个观测，159561 只有 564 个观测；均按上市后样本计算，不向前填充。面板的 `intraday_path` 在历史回补中为 `NOT_AVAILABLE_DAILY_BACKFILL`，故日内 T 标为证据不足。

信号只读到收盘 D 已形成的 close 序列；20 日均值使用 D 以前的 20 个收盘，5 日动量只作趋势加速保护；交易最早在 D+1 开盘，退出在 1/2/3/5/10 个交易日后的收盘。没有使用未来数据，也没有使用日线 high/low 伪造盘中先后。固定阈值扫描为 1%、1.5%、2%、2.5%、3%、4%，机动仓扫描为 10%、20%、25%、30%、40%、50%，成本为 10/20/30bp round-trip。

比较组包括 buy-and-hold、core+mobile、full swing 和固定 70% beta-control。beta-control 保留 30%现金，因此回撤下降不能被误称为 alpha。walk-forward 账面划分为 2024 calibration、2025 validation、2026 YTD holdout；最终默认参数预先固定为 2%／3日／25%，没有从单一最优点反推正式权限。

## 现有 11 只扫描

20bp 下，按 core+mobile 净收益排序的前三为 515880、561980、159781；但三者相对 buy-and-hold 的净增益分别为 -68.82、-51.03、-22.13 个百分点。588000 为 75.69% 对 97.18%，159781 为 98.78% 对 120.91%，561980 为 208.13% 对 259.16%。因此 H1（588000 是最优反复波段工具）为 `REJECTED`，H2（561980 的高振幅／趋势导致机械做 T 劣于 588000）为 `INSUFFICIENT_EVIDENCE`，H3（159781 在高 beta 与趋势风险间优于 561980）为 `REJECTED`。拒绝或不足均只表示本研究设计下未通过，不是对未来收益的保证。

最不适合“长期反复 T”的对象是 513180 恒生科技 ETF：20bp core+mobile 仅 7.55%，buy-and-hold 为 16.13%，且交易 73 次、换手 36.5 倍、回撤 -35.11%。低收益与高摩擦同时存在。

### 成本敏感性（现有池最高的 20bp 默认结果）

10bp、20bp、30bp 下第一名均为 515880，core+mobile 净收益分别为 222.70%、219.94%、217.42%；成本提高并没有改变结论方向，但三者仍低于其 288.76% buy-and-hold。成本并非把一个真实正 alpha 吞掉，而是进一步削弱本已落后的交易组合。

### 机动仓与策略结构

以 588000 为例，20bp core+mobile 在 10%／20%／25%／30%／40%／50% 机动仓时净收益为 88.58%／79.98%／75.69%／71.39%／62.79%／54.19%，均低于 97.18% buy-and-hold；25%—30%只是回撤与交易暴露的折中区间，不是稳定 alpha 区间。full swing 在 588000 仅 11.21%，远逊于 core+mobile；但它回撤、winner/right-tail 与错误回补损失更集中。综合来看 core+mobile 优于 full swing 的资本保留能力成立，优于 buy-and-hold 的净增益不成立。

## winner/right-tail、错误回补与状态边界

趋势加速、持续相对强势和真实突破时应关闭或缩小卖出型 T；高波动下跌但未出现承接确认时应延迟回补。当前日线面板没有完整 PIT 的市场 regime 标签，因此不能诚实地量化“哪一状态贡献最大”的因果结论，只能把趋势过滤作为保守防错门。20bp 默认下 588000 的 winner/right-tail 损失约 7.72%（按机动资本归一化），错误回补损失约 19.98%；561980 分别约 7.98%／21.11%；159781 约 6.33%／15.32%。损失结构显示反复交易的主要风险是卖飞和在弱势中重新买回，而不是手续费单独造成。

## 全市场候选筛选

候选发现扫描了本仓库现有研究数据中正式 11 只之外的全部可识别代码，共 1 只：159687。它只有 24 个观测（2026-07-13 至 2026-08-13），虽有约 3.58 亿元近 20 日平均成交额和 1.99%平均日振幅，但历史过短，所有深度策略结果均为 `INSUFFICIENT_EVIDENCE`。这不是对 A 股全市场当前 ETF 的穷尽性声明：本仓库没有提供可审计的全市场历史价格目录，外部网络数据也没有被拼接进 PIT 面板。因而没有新 ETF 能够在相同样本、成本、信号和 holdout 标准下击败现有最佳对象；159687 不建议加入观察池，也不得写入正式 universe。

T+0/T+1：现有数据未提供可审计的历史分钟路径、成交制度逐日标签、溢折价和海外时差联动，不能声称 T+0 提高了可实现净收益；日内机会数量、overnight gap 和溢折价风险无法在该数据边界内完成公平估计。

## MASTER 8.1 审查

`data_quality` 与 PIT 日线信号满足研究级要求；`sample_sufficiency` 对现有池大多满足、159687 不满足；`logic_stability`、`incremental_value`、`execution_translatability` 和 `benefit/cost` 未形成正向时间外证据；`production_availability` 不满足严格日内／制度层验证；`risk_non_increase` 不满足，因为卖飞和错误回补未被稳定压低。因此 MASTER 8.1 结论为 `RESEARCH_ONLY`，不生成 formal conversion review，不修改 MASTER，不修改 Dashboard 或生产 ETF universe，不新增交易权限。

## 可复现入口与残余风险

运行 `python research/backtests/analyze_etf_t_swing.py` 生成 `research/backtests/etf_t_swing_stage1_validation.json`。测试覆盖 no-lookahead、成本扣除、beta-control、正式 universe 不变和日内证据边界。残余风险包括：缺少历史分钟和 bid/ask、外部 ETF 全市场目录不完整、跨境 ETF 制度与溢折价未能逐日 PIT 对齐、2026 YTD holdout 尚短，以及当前分支基线的云端 main 同步受网络限制而保留了明确的远端 SHA 记录。
