# Formal Decision 定时任务从零重建说明 V1.0

## 目标

建立一个独立、可验证、可恢复的 Formal Decision 定时任务。它只新增任务入口与其必要的入口适配，不复制或改造【ETF系统复核】、【ETF交易复盘】的生产链；业务事实、BDS、completion、Formal review event、decision-notification、notification_center 和 PushPlus 仍只能由 current main 已登记的 canonical owner 负责。

本说明是工程验收清单，不替代 ETF_SYSTEM_INDEX.md、ETF规则_MASTER.md、数据规范、Formal Decision执行与用户回复协议、主动通知体系或生产变更协议。

## 任务实例与入口

必须存在一个全新的、独立的【ETF正式决策】自动化任务；旧任务只保留为暂停/封存记录，不参与运行。任务自身必须支持两种触发方式：

- 用户手动启动任务：source=CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS，entry_mode=MANUAL_SCHEDULED_TASK，任何北京时间可运行。
- 系统按日历自动唤起：source=CHATGPT_SCHEDULED_FORMAL_ANALYSIS，entry_mode=AUTOMATIC_SCHEDULED_TASK，只允许北京时间10:00、11:00、13:30、14:30。非交易日或不适用节点只 SKIP，不创建请求、不消费旧请求、不生成正文。

每次运行都必须取得新的 task_id、task_run_id、occurrence_id、parent_request_id、request_id、market_date。缺失或错配时 fail-closed；不得把任务名称、历史运行或当前时间推测成入口身份。

## 运行链

入口确认后，运行链必须严格为：

入口身份 → 唯一父请求 → request-bound snapshot/query_context/DWP/formal_reply_gate → WAIT/READY/TERMINAL → 完整业务判断 → 冻结一次 FINAL_CONTENT → 用户先看到同一正文 → BDS写入/回读 → completion生成/写入/回读 → 显式 workflow_dispatch → 当前 Formal review event → notification_id → PushPlus provider result。

WAIT 只继续消费同一父请求，不能创建第二请求或输出过程状态。READY 后不等待任何后置对象才开始业务判断。FINAL_CONTENT 之后的每一个后置对象都必须分别核实；不能用 BDS、market-snapshot、历史 completion 或历史 notification 代替 workflow_dispatch。

## 完整性和展示

业务回答必须覆盖同一 request-bound DWP 的全部 required_problem_ids，并逐一覆盖风险许可、三层监测、Discovery、全部观察ETF、机会ETF、全部实际持仓、持仓追加、现金、可释放资本、资金缺口、Trial/Confirm、实际部署金额、最终动作、关键风险和改变动作的条件。观察ETF缺少完整 thesis 时不得 RETAIN，不得从历史补写。

用户正文只含业务内容，不展示 request_id、task_id、workflow、路径、BDS、completion、provider日志或内部状态。通知正文必须逐字等于 FINAL_CONTENT。证券交易仍由用户人工确认和下单。

## 验收门

代码验收：入口脚本能在四个精确 UTC cron 下生成合法自动身份；手动身份不受节点限制；自动非节点 SKIP；同 occurrence 幂等；payload 同时提供 occurrence_id 和兼容所需 occurrence，且二者一致。

运行验收：全新自动化任务在一个正式节点成功完成完整链；必须分别核实父请求、request-bound READY、FINAL_CONTENT、BDS回读、completion回读、workflow_dispatch真实run、Formal review event、notification_id和当前 provider result。任一环节不确定只能记录未核实，不能宣称闭环。

变更边界：不修改【ETF系统复核】、【ETF交易复盘】的任务、调度、代码、协议、状态、completion或通知链；不新增 workflow、state、producer、checker、writer、provider、交易权限或证券订单。
