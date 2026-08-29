# ETF系统复杂度审计第二批：health guard 与分钟验证链

基线：main `161c2554cb48ad956c42aa80f8e9d1b75378b69c`。

本报告只在隔离审计分支形成，不修改main生产行为。

## 1. lightweight production health guard

处置：`RETIRE_CANDIDATE`，生产动作当前为 `OBSERVE`。

### 当前事实

`production_health_guard.py` 由 `self-healing-watchdog.yml` 的每一次watchdog运行无条件执行。watchdog当前工作日cron合计约88个计划触发点（另有workflow_dispatch和path push）。因此该只读聚合脚本并非“偶尔人工查看”，而是高频附着在现有恢复链上。

该脚本读取的主要状态均已有各自canonical owner：system consistency、A股runtime、海外runtime、US runtime、self-healing status、maintenance/account reconciliation、execution reconciliation、E2E、notification center。脚本自身只打印聚合JSON：
- 不持久化独立状态；
- 不触发恢复；
- 不dispatch workflow；
- 不直接通知用户；
- 不改变任何已有owner的判断；
- watchdog后续控制流也不消费该聚合结果。

因此它当前更接近“每次watchdog都生成一次无人消费的日志视图”，而不是生产控制面。

### 独立价值判断

当前未发现该聚合结果成为任何正式恢复、通知、门禁或E2E输入。它最初用于提供“一眼看全”的诊断视图有合理建设期价值，但该价值目前没有形成机器消费合同，且核心信息都已存在于原状态文件。

若后续没有人工/自动消费者依赖其stdout日志，则继续在约88个工作日计划watchdog节点执行的边际价值偏低。

### 建议

正式窗口允许后优先执行最小退役：
1. 从 `self-healing-watchdog.yml` 删除无条件 `Run read-only comprehensive production health aggregate` step；
2. 删除 `scripts/production_health_guard.py`，或若仍有明确人工诊断需求，则改为仅 `workflow_dispatch` / 本地诊断工具，不挂高频生产watchdog；
3. 同步移除watchdog path触发对该脚本的监听；
4. 不新增替代状态、workflow或checker；
5. 验证self-healing、failure guard、full consistency、E2E、PushPlus现有路径行为零变化。

该动作会改变生产workflow执行内容，虽然不改变交易/行情事实合同，仍按当前CA05作为独立非紧急生产行为调整等待，不现在合main。

## 2. `minute-source-poc.yml`

处置：`NO_CHANGE`。

该workflow无schedule，只在人工dispatch或相关文件push时运行，artifact-only且contents read。正式分钟能力已生产化并不自动意味着这个低频能力验证入口应该删除；其持续运行成本接近零，并可用于未来minute source实现变更时重新做capability validation。

除非未来确定其脚本已无法验证任何仍可能变化的能力，否则现在删除收益很小。

## 3. `akshare-capability-poc.yml`

处置：`NO_CHANGE` / 后续低优先复核。

同样无schedule，只在人工dispatch或自身相关文件push时运行，artifact-only。虽然当前AkShare不是正式分钟主生产链，但该PoC没有造成高频provider调用或状态扰动。当前没有足够收益理由删除。

## 4. `minute-path-shadow.yml`

处置：`MEASURE_FIRST`。

该workflow仍有5个逻辑节点，每个节点配置primary + recovery，共10个工作日cron。一次run同时承担：
- ETF分钟shadow；
- 指数分钟与10分钟承接PoC；
- A股个股分钟生产语义probe；
- 分钟证据对正式context集成验证。

其中多个能力已进入正式生产，但shadow仍可能捕捉producer local guard之外的质量漂移，因此不能仅凭“已经生产化”直接删除。

下一步应读取近期真实run/artifact，回答：
1. 最近若干交易日是否发现过正式生产门禁未发现的独立异常；
2. primary/recovery双脉冲实际是否经常避免GitHub schedule漏跑，还是大部分只是重复执行；
3. 四类probe是否仍都需要5个逻辑节点；
4. 是否可以保留一个窄shadow验证入口、减少已生产化能力的重复probe，而不削弱PIT/连续性/quote alignment反漂移能力。

在没有这些运行证据前不降频、不拆workflow。

## 5. 第二批结论

本轮已经得到一个可以明确排队的生产候选：`production_health_guard.py` 高频无消费聚合具备退役资格；但当前属于非紧急生产workflow行为变化，按CA05保持OBSERVE。

同时避免两类过度清理：
- 不因为名字带PoC就删除无schedule、artifact-only的低成本验证入口；
- 不因为分钟能力已经生产化就直接删掉仍可能提供独立反漂移信息的shadow。

原则仍是：该变更就变更，该测量就测量，该等待就等待；以实际边际价值而不是文件数量决定复杂度回收。