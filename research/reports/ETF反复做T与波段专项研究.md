# ETF反复做T与波段资本效率专项研究

## 长期日线层

本轮已修正 inventory/release/rebuy 会计：mobile=25% 时初始为 75% core ETF + 25% mobile ETF，合计初始 ETF 暴露 100%；mobile 只有在 release 后才进入 `MOBILE_CASH`，只能在后续合法信号中 rebuy。旧版“75% ETF + 25%闲置现金再抄底”不再作为核心策略。

canonical `events/research/daily_features` 约646个交易日，正式11只全部重跑。D 收盘形成信号，D+1 开盘成交；hold 是 release 后最小现金持有期，扫描 1/2/3/5/10 日、1/1.5/2/2.5/3/4% 阈值、10/20/25/30/40/50% mobile 和 10/20/30bp。20bp、预注册 25% mobile/3d/trend-filter 的相对 B&H alpha：

| ETF | B&H累计收益 | core+mobile净收益 | 相对B&H alpha |
|---|---:|---:|---:|
| 561980 | 260.21% | 199.04% | -61.17pp |
| 588000 | 98.42% | 75.21% | -23.21pp |
| 159781 | 124.02% | 103.28% | -20.73pp |
| 515880 | 298.22% | 233.45% | -64.77pp |

其余对象轻微正值仅 159561 +2.43pp、159992 +2.37pp、513180 +1.32pp，均不足以称稳定 alpha。按资本效率与风险边界，现有池前三为 513180、159561、159992，但均为 RESEARCH_ONLY；最不适合为 515880，其次 561980。full swing 未胜 B&H；beta-control 为 70% ETF + 30%现金且零策略交易。mobile 10—50% 未形成稳定正 alpha 区间；hold 1/2/3/5/10 日改变路径但未改变资格。

## 历史15m日内层

已从旧记录和 artifact 核实新浪路径，并实际即时请求公开历史 K 线接口。provider 为 `SINA_15M_RESEARCH_ONLY_NOT_PRODUCTION_PROVIDER`；artifact 9734210770（run 33318592790）仍未过期但不含原始 bars。当前恢复结果为 11/11 对象、每只 1023 根 15m bar、64—71 个交易日（因最新交易日窗口变化而异）。该脚本不创建 provider、分钟仓库、workflow 或 production state。

日内模型使用 completed bar close signal -> next bar open execution；不使用同一 bar high/low 内部顺序。mobile 初始 invested，release 后才能 rebuy；自然条件未触发时按预注册日终固定收盘规则强制回补，避免现金跨日免费重置；T+1 不允许当天新买份额再卖出；费用、right-tail 和 wrong-rebuy 均单独记录。no-trend-filter、trend-filter、multiple-cycles 对照已运行，hold 1/2/4/8 bars 与 rest-of-day 已运行。

hold=4 bars 的逐日平均相对 B&H alpha（百分点）在 20bp 下为：561980 -0.032pp、588000 -0.042pp、159781 -0.022pp、515880 -0.035pp；10/20/30bp 均未产生稳定正 alpha。严格日内层对每个 release 执行日终固定收盘回补，因此 unclosed_cycles=0，回补两腿费用均计入；T+1 multiple-cycle 版本禁止回补当日再次 release。多次 T 仍未显示可兑现优势。逐日 T+0/T+1 制度元数据不足，不能宣称 T+0 有额外价值；跨境 ETF 还存在时差、溢折价、底层休市风险。

## 两层交叉结论

日线与15m方向一致：趋势强、右尾大的 561980/515880 日内也没有抵消卖飞和费用；588000 未成为稳定最优；159781 日内短样本略优于 561980，但仍为负且不足以证明优越。当前结论是 `FAIL / RESEARCH_ONLY`，不是 PASS；日内短样本不能替代跨年度稳定性。

right-tail 只从 release 到 rebuy/样本结束计算，输出 max、mean、median、累计 portfolio drag；wrong-rebuy 只从真实 rebuy 之后计算。事件 JSON 保存 release/rebuy 时间、价格、bars、两腿费用、完成状态和 unclosed_at_day_end。

## 外部候选与治理结论

外部全市场仍为 `EXTERNAL_MARKET_SCREEN_INCOMPLETE_DATA_LIMITATION`：池外可审计历史样本只有 159687、24 个观测，不能声称全市场筛选完成，也不能声称没有更优新 ETF，不修改正式 universe。

MASTER 8.1 保持 `RESEARCH_ONLY`，不生成 conversion review，不增加交易权限。未修改 MASTER、Dashboard、正式 ETF universe、provider、workflow 或 production state。

## 文件与验收

- `research/backtests/etf_t_swing_stage1_validation.json`
- `research/backtests/etf_t_swing_intraday_sina.json`
- `research/backtests/analyze_etf_t_swing.py`
- `research/backtests/analyze_etf_intraday_sina.py`
- `tests/test_market_etf_t_swing.py`

最终判断：现有11只没有通过严格成本、时间外和右尾约束的稳定做T工具；外部全市场筛选仍未完成，不能将该结论外推到全市场。
