# Hyperliquid对象级消费与决策贡献协议_V1.0

## 1. 目的与边界

本协议只规定 Hyperliquid 外部辅助证据如何被正式判断逐对象消费、记录和表达。它不改变交易规则、风险许可、Trial／Confirm、金额档、卖出规则、账户事实、交易权限或通知链；Hyperliquid不得独立生成交易动作。

Hyperliquid的价值不以“是否单独改变最终动作”衡量。以下均属于可记录的决策贡献：直接支持、方向反向、与A股事实重复确认、时机过滤、拥挤／流动性风险提示、对象映射缺失导致的不可用，或明确无关。


## 与主动通知的关系
Hyperliquid对象级消费账本与用户主动通知是两个层级。对象有映射、证据发生变化，并不自动意味着应发送PushPlus；只有该变化可能改变当前持仓、机会ETF、观察ETF、Discovery候选或现金竞争的下一次正式判断时，才取得用户通知资格。无映射、无当前对象交集、仅目录/覆盖/provider变化或仅技术字段刷新，均只记录在机器账本，不生成用户通知。
## 2. 必须按对象消费

正式判断涉及Discovery、观察ETF、机会ETF或实际持仓时，必须对每个实际需要评估的对象执行一次对象级映射：

- 关联的Hyperliquid资产及经济暴露族群；
- mapping_reason：为什么该资产可以代表该ETF或持仓的外部暴露；
- observation_as_of：采集完成时间及北京时间；
- provider_timestamp_status：若接口不提供事件时间，必须明确使用可见状态边界，不得伪造事件时间；
- evidence_status：READY、DEGRADED、FAILED、NOT_APPLICABLE；
- consumption_status：CONSUMED、CONSUMED_AS_LIMITATION、NOT_CONSUMED；
- decision_effect：SUPPORT、CONTRADICT、CONFIRMATORY、TIMING_FILTER、CROWDING_RISK、NO_DIRECT_MAPPING、NO_INCREMENTAL_CHANGE或INSUFFICIENT；
- evidence_summary：实际方向、未平仓量、成交量、资金费率／溢价等本次可见字段；
- decision_use：它改变或维持了哪一项判断，不能只写“辅助参考”。

对象没有直接映射时，应记录为NO_DIRECT_MAPPING并解释不能用于该对象的独立确认；不得笼统写成“本次无明确增量价值”。

## 3. 贡献分类

“无明确增量价值”仅可用于已经完成映射、事实质量通过、与A股及其他正式事实比较后，确认其既没有新增支持、反向、重复确认、时机过滤或风险提示的对象。以下情况不得使用该表述替代具体贡献：

- Hyperliquid与A股同向：写明CONFIRMATORY，说明强化了方向或风险判断；
- Hyperliquid与A股反向：写明CONTRADICT，说明降低信号可靠性或阻止升级；
- 仅提供跨市场时机信息：写明TIMING_FILTER；
- 未平仓量、成交量或其他字段显示拥挤／异常：写明CROWDING_RISK；
- 采集失败、覆盖不足或时间边界不合法：写明DEGRADED／FAILED／INSUFFICIENT及其限制；
- 无直接映射：写明NO_DIRECT_MAPPING，不把“没有可用证据”写成“证据没有价值”。

## 4. completion结构化交接

Formal Decision completion在已有字段之外，必须写入统一的只读证据消费账本 evidence_consumption_audit；其中的 hyperliquid 子账本承载本次 Hyperliquid 对象级消费结果。不得另建第二套 completion 账本。每条 Hyperliquid 记录至少绑定：

- task_run_id、occurrence_id、market_date；
- object_type、object_id、object_name；
- mapped_assets、mapping_reason；
- observation_as_of、evidence_status；
- consumption_status、decision_effect；
- evidence_summary、decision_use。

该账本是正文的可回溯机器摘要，不是第二事实源，不得覆盖canonical市场、账户或交易事实。若本次运行环境无法取得某字段，必须记录缺失及原因，不得用历史值补齐。

## 5. 正文表达

正文应优先回答三个问题：

1. 本对象实际关联了哪些Hyperliquid证据；
2. 证据对本对象是支持、反向、确认、时机过滤、风险提示还是不可用；
3. 它是否改变了当前动作、金额、Trial／Confirm或继续观察理由。

若Hyperliquid未改变最终动作，也必须说明“维持原判断的具体原因”；不得把“没有改变动作”写成“没有贡献”。Hyperliquid仍不得单独授予交易权限、Trial、Confirm、金额或卖出份额。

**用户可见正文最低信息量：** 对已映射对象，正文至少逐对象呈现：外部资产名称及中文代表方向、实际观测到的信号、与A股对象的同步/背离关系、该证据对机会强弱或入场时机的具体影响，以及未改变交易动作的独立理由。禁止用“无明确增量价值”“仅作辅助参考”“方向矛盾”等概括性短语替代上述解释；这些词只能作为结论标签，后面必须紧跟具体事实和决策作用。对无直接映射对象，必须说明无映射原因和因此不能用于该对象判断。

## 6. 验收要求

下一次合并后正式决策必须逐项检查：

- completion存在非空 evidence_consumption_audit，且其中 Hyperliquid 子账本覆盖实际评估对象；
- 覆盖实际持仓、观察ETF、Discovery候选和机会层中本次实际评估对象；
- 每条记录有对象身份、映射资产、证据状态、消费状态、决策作用和映射理由；
- 正文不再对已产生确认、反向、时机或风险作用的对象统一写“无明确增量价值”；
- 159217等正式管理持仓仍按账户事实独立识别，不因Hyperliquid字段缺失被删除或降级；
- 仍不生成自动交易，不改变通知链和三类任务独立性。
