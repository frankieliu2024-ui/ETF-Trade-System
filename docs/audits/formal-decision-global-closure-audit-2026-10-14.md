# Formal Decision 全局闭环审计与集中修复记录

基准分支：current `main`（审计时点 2026-10-14）

## 结论

本次真实对话暴露的主因不是 PushPlus 延迟，而是 READY 业务回复与 BUSINESS_DECISION_SOURCE 交接之间缺少可回读的强制边界。READY 只产生了可供 Actor 判断的 ready_handoff 描述，但没有保证当前对话一定提交、回读并锁定当前 parent request 的 BDS。随后若继续交接，历史请求仍可能成为候选输入。

## 当前唯一业务主链

`parent request → request-bound snapshot → query_context → DWP/problem graph → Actor answers → BUSINESS_DECISION_SOURCE → Decision Fact → FORMAL_DECISION completion → REPORT → REPORT_DELIVERY_REQUEST → notification_center → 当前 notification → provider result`

READY 只能表示业务判断可以开始，不能表示 completion、REPORT 或 notification 已完成。后置链缺少当前 request 绑定时必须 fail-closed，不能回退到旧请求、旧 BDS、旧 completion 或旧 REPORT。

## 已确认的风险点

1. `formal_request_consumer.py` 能返回 READY handoff，但此前必答 payload 清单未明确要求 entry identity、task/occurrence identity、content_hash 和 target path，外部 Actor 可以在自然语言回复后缺少结构化交接。
2. `manual_formal_decision_completion.py` 已有 BDS builder，但此前生成的 Actor envelope 未完整携带 entry_mode、task identity、occurrence identity、presentation content hash 和明确目标路径，导致当前请求边界主要依赖调用方正确传参。
3. `build_e2e_status.py` 仍写入全局 `data/state/e2e_status.json`；System Review 或其他运行态流程可以覆盖全局 gate 的可见状态。该文件不能作为多个并发 Formal Decision 的唯一生命周期事实。
4. REPORT 逻辑已有精确事件路径约束，但兼容路径仍存在目录扫描风险；任何旧事件扫描都必须被当前 completion 的 request_id、decision_id、report_id 和 content_hash 共同约束。
5. 受控业务合同通过只能证明代码合同，不能证明真实对话 Actor 已完成当前请求的 BDS、completion、REPORT、notification 和 PushPlus 交接。

## 本次集中修复范围

本 PR 只加强已有 Formal Decision producer/consumer 的当前请求边界：

- READY handoff 显式列出当前请求 BDS 所需的入口身份、任务身份、市场日期、答案、决策身份、消费快照、冻结正文、content_hash 和目标路径。
- BDS builder 根据三种正式入口映射唯一 entry_mode，并写入明确的 target_bds_path 与 content_hash。
- 不新增 workflow、producer、checker、notification chain、ETF 任务、交易权限或新的验收层。
- 不修改 MASTER、provider priority、monitor universe 或下单能力。

## 尚未在本 PR 中宣称完成的事项

- 真实对话入口的当前请求级 BDS、Decision Fact、completion、REPORT、REPORT_DELIVERY_REQUEST、notification 和 PushPlus provider result 尚未因本 PR 自动产生。
- 三种入口的真实运行证据必须在合并后通过一次最小业务验收分别取得。
- 全局 `e2e_status.json` 的多请求并发投影隔离仍需在后续集中范围内继续验证；不能以本文件或 CI PASS 代替真实证据。

## 状态语义

在三入口真实当前请求证据完整前，系统状态保持：

`MERGED_BUT_NOT_ACCEPTED`
