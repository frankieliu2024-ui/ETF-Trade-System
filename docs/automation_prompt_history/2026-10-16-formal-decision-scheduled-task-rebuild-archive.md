# Formal Decision 定时任务重建封存记录

日期：2026-10-16（北京时间）

## 封存对象

本记录封存此前名为“ETF正式决策”的自动化任务指令、入口解释和失败修补链。封存对象不再作为新任务的规范来源；current main 的正式 Formal Decision 协议、通知协议和既有 canonical owner 仍然有效。

旧自动化实例：6acb1aa80db4819181dcf03d2efd18d0  
旧实例状态：已暂停，不再作为新的 Formal Decision 定时任务入口。

## 封存原因

此前任务把用户手动启动的定时任务与系统自动日历触发混淆；曾在入口身份、观察 ETF thesis、completion handoff 和自动节点判断之间反复叠加局部修复，导致正文可以生成但后置交接不稳定。旧指令还把模型无法直接读取底层 automation 元数据当成入口身份未建立，不能继续作为新的任务设计基础。

## 新建边界

新的 Formal Decision 定时任务只复用 current main 已有的：

- 三入口身份合同；
- request-bound snapshot/query_context/DWP/formal_reply_gate；
- BUSINESS_DECISION_SOURCE；
- Formal completion ingress；
- events/reviews/formal_<occurrence_id>.json；
- decision-notification、notification_center 和 PushPlus provider 链。

本次重建不得新增第二套 workflow、producer、state、completion、REPORT、event、writer、provider 或通知链，也不得修改【ETF系统复核】、【ETF交易复盘】及其定时任务、代码和协议。

## 对照基准

新任务的调度与用户可见行为参照系统复核和交易复盘：任务正文优先、唯一 FINAL_CONTENT、后置 completion/event/notification 投影、各状态分别核实；Formal Decision 的业务答案仍必须严格服从 current main 的 DWP 和 MASTER，不以复盘规则替代交易规则。

## 运行原则

用户手动启动该任务时使用 MANUAL_SCHEDULED_TASK，任何北京时间均可执行；系统无人手动按正式日历触发时使用 AUTOMATIC_SCHEDULED_TASK，仅允许 current main 日历规定的正式节点。任何入口都必须创建新的当前身份，不消费历史请求或历史正文。
