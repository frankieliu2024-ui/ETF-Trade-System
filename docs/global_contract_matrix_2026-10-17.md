# 全域契约矩阵（2026-10-17）

本文是 Issue #1447 的第一阶段审计产物，记录 current main 已确认的业务边界、canonical 输入、派生状态、生产 owner 和必须验证的相邻断点。本文不新增规则；正式规则仍按 `ETF_SYSTEM_INDEX.md` 路由读取。

| failure domain | canonical 输入/事实 | 当前主要 owner/入口 | 必须保持的不变量 | 当前审计结论 | 下一步验收 |
|---|---|---|---|---|---|
| 对象生命周期 | `config/market/etf_monitor_universe.json`、账户事实、观察/Discovery输入 | `provider_policy.py`、`cloud_runner_snapshot.py`、`observation_etf_management.py`、Discovery脚本 | 新增持仓/观察ETF自动获得有效身份、provider、PIT/freshness和资格；特殊对象可显式覆盖；非法对象fail-closed | 默认provider策略已统一到部分消费者，但仍需盘点所有直接读取者 | 新增未知A股ETF、观察ETF、特殊对象和非法对象的全链路准入测试 |
| 行情事实与provider降级 | `CURRENT.json`、市场监测配置、provider priority | market snapshot、query refresh、emergency evidence、legacy query路径 | 同一对象使用统一路由；fallback只改变质量解释，不改变事实时点或交易权限 | 存在多个脚本直接读取配置/状态，需收敛到统一解析接口 | 新对象+主源失败+多源失败+陈旧事实的组合测试 |
| 研究与派生特征 | close facts、research execution bridge | `build_research_features.py`、`build_strategy_equity...` | 派生结果绑定 source identity、market date、cutoff、generated_at、freshness/alignment和usable status | 风险证据、strategy equity、research bridge存在独立重建入口 | 盘后重建、旧估值截止日、缺失补采、降级传播测试 |
| 风险证据与权限 | risk evidence、MASTER风险规则 | `build_skfolio_risk_evidence.py`、e2e projection | 风险证据截止日不得落后却被标记最新；禁止新增必须由正式证据支撑 | 10月17日暴露的旧估值截止日与降级状态说明需要上游门禁 | 新鲜/陈旧/待补/恢复四态，以及权限投影一致性测试 |
| 三类正式任务 | task/request identity、PIT facts、FINAL_CONTENT | scheduled/manual入口、state-sync owner、completion owner | 三类任务身份、正文、completion、event、REPORT、notification严格隔离 | 现有主链规则完整，但需确认旧/compat入口不能绕过 | 新 request、重复 request、旧 completion、跨任务对象拒绝测试 |
| 正文到通知 | frozen FINAL_CONTENT、completion binding | canonical event → REPORT → notification_center → PushPlus | 所有后置对象逐字复用同一正文；provider成功不等于用户收到 | 需验证所有固定报告均无第二正文和历史回退 | 三类报告逐层回读、失败重试、provider失败恢复测试 |
| 生产写入与并发 | latest main、mutation protocol | workflow、Contents、formal mutation gateway | 每个canonical对象有唯一writer；并发不取消合法写入；失败可归因 | 多 workflow/script 存在直接写状态路径，需形成完整writer inventory | 并发运行、重试、取消、旧分支写入和main回读测试 |
| Legacy/compat路径 | 旧请求、旧状态、旧脚本 | legacy query、account、emergency、recovery脚本 | 兼容只用于登记的恢复边界，不能成为第二生产链 | 已发现多个直接读取 universe/config/state 的候选路径 | 逐路径标记 production reachable / recovery-only / dead code，并做禁用或收敛 |
| 系统健康投影 | e2e/system consistency/review context | `build_e2e_status.py`、consistency checker | 下游降级不得被投影为完成、关闭、最新或可执行 | 10月17日状态降级暴露跨层传播需逐字段核对 | 从原始事实到Dashboard/e2e/notification的链式断言 |
