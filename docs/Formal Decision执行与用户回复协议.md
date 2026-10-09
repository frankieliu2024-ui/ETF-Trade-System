# Formal Decision执行与用户回复协议

## 1. 目的与范围

本协议只规定 Formal Decision 的请求消费、业务回复时序和用户可见边界。交易规则以 `ETF规则_MASTER.md` 为准，事实、PIT、freshness 和三层监测以数据规范为准，代码与生产变更以生产变更协议为准。本协议不新增 workflow、state、producer、checker、writer、provider 或第二决策链。

适用范围：用户发起正式决策、要求重新判断、询问是否买卖/调仓、现在怎么办或盘中分析。System Review、Formal Review、Technical Audit 和普通行情问答不适用本协议。

## 2. 唯一父请求

每次独立 Formal Decision 对应一个唯一父请求。重复催办、重试和“继续执行”必须恢复并消费同一父请求，不得创建等价请求、重复刷新或建立平行完成链。只有用户明确要求新的独立判断，或原请求已经终结后再次判断，才建立新请求。

## 3. 唯一三态输出合同

Formal Decision 对用户可见行为只有 WAIT、READY、TERMINAL 三种状态，不存在第四种解释状态。

### WAIT

同一父请求仍处于 queued、building、in-flight，或当前只能读取旧、空、未绑定的派生上下文。

必须：

- 不产生任何用户可见输出；
- 继续消费同一父请求；
- 不创建第二请求；
- 不输出失败、等待、处理中或内部状态说明。

旧上下文、空上下文、连接器暂时空读、一次超时和 request_id 尚未匹配，只能解释为 WAIT；除非已有明确 terminal failure，不得提前终止。

### READY

同一父请求的合法 request-bound facts、DWP、PIT、freshness、market phase 和业务判断前置条件已经满足。

必须：

- 立即完成完整业务判断；
- 第一条用户可见回复直接输出完整 Formal Decision 长报告；
- 不等待持久化、审计、通知、consistency、acceptance 或其他后置投影；
- 不重复读取原始行情、重复 Discovery 或重新审计已经通过的生产链。

### TERMINAL

同一父请求存在明确 terminal failure，且不存在可以继续消费的合法链。

才允许：

- 输出一次终止说明；
- 说明真实原因和必要的人工下一步；
- 不将非终态读取结果解释为终止失败。

## 4. READY前的业务门禁

READY 前必须完成或按正式规范合法降级：

- request-bound facts、PIT、freshness 和 market phase；
- 海外与跨市场、A股内部反馈、ETF与资本机会三层监测；
- 全市场机会发现、观察 ETF、机会 ETF；
- 全部实际持仓逐一判断；
- 现金、可释放资本和完整资本竞争；
- 符合 MASTER 的风险、Trial、Confirm、金额、集中度和卖出判断。

本协议不复制上述规则，只要求按所属正式文件读取和执行。用户最终决定并人工下单，ChatGPT不得自动提交证券订单。

## 5. 回复与后置生产分离

合法 READY 后，执行顺序为：

`request-bound facts → 业务判断 → 用户长报告 → 后置持久化、审计、通知和验收`

后置步骤不得阻塞、撤销或改写已经取得合法业务回复资格的判断；后置闭环未完成时，也不得宣称整个事项已经完成闭环。

## 6. 用户可见边界

正式回复只表达业务结论、数据时点、三层监测、持仓动作、资本用途、风险、后续关注点、置信度和最可能出错的地方。

不得向用户展示 request_id、文件路径、commit、workflow、run ID、provider 日志、canonical、BDS、writer、Decision Fact、consistency、acceptance、内部枚举、重试记录或“自动接管”等控制面术语。

## 7. 验收边界

Implementation Acceptance 只能证明仓库实现、测试、PR、CI 和必要的 latest-main 验收符合本协议；不能单独证明真实 ChatGPT 交互路径已经遵守协议。

Real Target-Path Acceptance 必须验证：

- WAIT期间用户端保持零输出；
- 同一父请求随后达到 READY；
- 第一条用户可见消息直接是完整业务报告；
- READY 后不等待后置生产闭环；
- 不创建第二父请求。

在真实目标路径验收通过前，不得仅凭 helper、单元测试或 CI PASS 宣称本故障域已经闭环。
