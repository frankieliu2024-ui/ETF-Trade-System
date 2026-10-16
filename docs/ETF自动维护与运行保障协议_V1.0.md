# ETF自动维护与运行保障协议 V1.0

> 本协议统一ETF系统自动维护的触发、路由、canonical owner、回读、幂等、失败和人工接管边界。
> 本协议不是交易规则、市场数据规范、主动通知规范、Formal Decision业务协议或生产变更准入协议；它只负责把自动运行场景接到既有规范和既有owner。

## 1. 唯一目标

所有自动维护必须实现同一闭环：

**触发事件 → 入口分类 → 身份/来源校验 → 当前main事实读取 → 唯一canonical owner → 幂等写入 → 立即回读 → 下游投影 → 维护回执**

任何只完成请求写入、workflow success、局部文件写入或 provider success 的结果，都不能单独表示维护闭环完成。

## 2. 适用场景与唯一owner

|场景|标准输入|唯一owner|主要输出|
|-|-|-|-|
|券商账户/持仓截图|requests/live_snapshot/**|账户事实同步owner sync_formal_files.py及既有账户状态链|data/state/account_fact.json、Dashboard|
|券商对账单/费用确认|requests/trade_fact_correction/**|apply_trade_fact_correction.py|成交事件、费用、策略净值、行情档案、经验库、Dashboard|
|真实成交事实|成交事实请求或events/trades/**|process_state_sync_request.py及成交事实owner|events/trades/**、账户/档案投影|
|行情刷新/即时补采|市场刷新请求或既有行情节点|cloud_runner_snapshot.py、query_time_market_refresh.py及市场数据owner|行情快照、质量和request-bound上下文|
|系统复核|既有System Review入口|既有System Review owner|复核事实、报告和主动通知|
|交易复盘|既有Trade Review入口|既有Trade Review owner|复盘事件、行情档案和经验投影|
|正式决策后置交接|Formal Decision completion ingress|既有completion owner与decision-notification.yml|event、REPORT、notification、provider result|
|研究/历史事实回补|既有research/backfill request|对应research/backfill owner|研究或历史事实，不直接扩大权限|
|一致性/可靠性审计|push、schedule或手动审计|既有consistency/checker owner|一致性报告、失败归因和验收证据|

不得为以上场景新增第二个producer、checker、writer、workflow或状态副本。若发现现有owner不能覆盖场景，先走生产变更协议的准入和owner审计。

## 3. 输入和身份合同

每个自动维护输入至少应能确定：

- request_id或等价唯一事件ID；
- source、interaction_scenario或等价入口；
- requested_at_beijing或事实发生时间；
- 输入来源与事实时点的区分；
- 当前运行所绑定的main版本；
- 幂等键或唯一事实签名。

缺失、冲突、历史复用或无法确认时必须fail-closed。不得根据文件名、任务名称、计划时间、普通行情时间或聊天记忆猜测身份。

## 4. Owner与写入边界

1. workflow只负责触发、准备环境、调用owner、提交owner生成的结果和暴露回执。
2. workflow不得直接推导交易结论，不得直接写正式经验、Dashboard、通知、REPORT或PushPlus。
3. 业务脚本只能在已登记的canonical owner职责内写入；三份人类可读正式事实文件必须经过formal_file_mutation_gateway.py。
4. ETF规则_MASTER.md永远不可由自动维护链写入。
5. 自动维护不得修改交易权限、provider priority、正式监测范围或用户人工下单边界。
6. Formal Decision、System Review和Trade Review必须保持各自业务链；本协议只规定运行接线，不改变其事件语义。

## 5. 幂等与历史边界

同一输入重复消费必须满足：

- 不产生第二成交、第二费用、第二CASE、第二正文、第二event或第二通知；
- 已确认费用再次提交只能得到幂等成功；
- 已存在冲突事实必须停止并进入人工复核；
- 历史请求、历史报告、历史正文和历史状态不得绑定为本次运行；
- 任何重试必须继续使用原身份和原输入，不得新建等价请求；
- 投影可重建，canonical事实不可被派生投影反向覆盖。

## 6. 回读合同

Owner返回成功后，维护链必须回读并核对：

- 输入身份与唯一键；
- canonical事实或事件；
- 所有本次声明会更新的下游投影；
- content_hash、事实时间、来源和状态；
- 维护回执中的status、owner、readback_status和错误分类。

只要任一必需回读失败，整体状态必须是MAINTENANCE_INCOMPLETE，不得表述为已更新完成，也不得自动补发历史数据。

## 7. 失败、重试和人工接管

失败统一分为：

- SKIP_NOT_APPLICABLE：入口合法但当前节点不适用；
- RETRY_SAME_INPUT：暂时性网络、锁或workflow失败，允许沿原身份重试；
- CONFLICT_REQUIRES_REVIEW：事实冲突、重复身份或历史绑定，禁止自动覆盖；
- OWNER_REJECTED：schema、PIT、权限或来源合同失败；
- PROJECTION_INCOMPLETE：canonical事实成功但下游投影未闭环；
- MAINTENANCE_INCOMPLETE：结果未知或回读失败。

禁止把失败转换成静默成功；禁止在失败后自动补发历史正文、创建第二通知或切换到旁路writer。达到人工接管条件时，应保留原输入、错误分类和最近一次回读证据。

## 8. 维护回执与可观测性

每个自动维护场景至少应产生可恢复的运行证据，优先复用既有状态或receipt，不新增平行状态库。回执字段：

request_id、source、owner、input_hash、started_at_beijing、finished_at_beijing、status、readback_status、projected_paths、error_class。

回执不得代替canonical事实，也不得成为第二业务规则来源。

## 8.1 Scheduled Review通知投影回读

System Review和Trade Review均属于Scheduled Review投影场景。正文冻结后，唯一owner必须按`SCHEDULED_REVIEW_COMPLETION`完成后置交接，并至少回读：completion request、canonical review event、REPORT、notification_id和provider result。正文已生成但缺少completion、event、REPORT或notification_id时，必须归类为`PROJECTION_INCOMPLETE`或`MAINTENANCE_INCOMPLETE`，不得表述为通知完成，不得自动补发历史正文或创建第二通知。

## 9. 场景验收矩阵

每个场景必须验证：

1. 正常输入能够到达唯一owner；
2. 缺字段、身份冲突、重复输入和历史输入均按合同处理；
3. owner成功后所有声明投影均可回读；
4. workflow成功但owner失败、回读失败或投影缺失时不会报告成功；
5. 重试不产生第二对象；
6. 不触及MASTER、交易权限、provider priority及无关复核链；
7. current main变更后仍能从INDEX和本协议恢复执行路径。

## 10. 维护与版本边界

本协议只登记稳定的自动运行边界。具体节点、provider、监测全集、交易规则和通知资格必须分别读取其唯一规范或机器事实。新增场景必须先查重、确定owner和净复杂度，再通过生产变更协议实施；不得通过本协议偷偷新增workflow、state、writer或业务链。

协议更新必须保留旧版本、变更原因、哈希和回滚位置，并通过独立分支、测试、PR、CI和merged-main验收。
