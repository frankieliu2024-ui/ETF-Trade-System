# ETF 三类任务冻结证据索引

本文件是冻结包内部的导航说明，不是新的规则源。唯一规则源仍为 `current main` 的 `ETF_SYSTEM_INDEX.md` 及其登记协议。

## 唯一读取入口

1. `docs/freezes/ETF_THREE_TASKS_CURRENT_FREEZE.json`
2. 本文件登记的 `MANIFEST.json`
3. 三份当前 Prompt 原文快照
4. `schedule-readback.json`
5. `code-map.json`
6. `recovery-runbook.md`

## 三类证据

- Prompt：三条任务当前 live Prompt 的完整原文；仓库文件的 blob SHA 只用于仓库证据变更检测。
- Schedule：云浏览器当前界面可见的启用状态、重复频率和任务身份；平台未提供稳定 Schedule JSON，因此不推断内部时间配置。
- Runtime：必须按新的真实运行身份回读 FINAL_CONTENT、completion、event、REPORT、notification、provider result；历史运行不能替代。

## 封存状态判定

只有 Prompt、Schedule、仓库代码映射、恢复路径和新鲜运行证据均具备，并经过相关 CI，才允许评估最终封存。平台原生 hash 未提供时，不得声称平台配置不可漂移；应明确记录“仓库侧证据完整、平台原生指纹不可得”。

## 恢复边界

恢复时先读取唯一冻结指针，再按 task_id 和本次运行身份筛选对象，最后用 content_hash 绑定正文与下游对象。不得按文件名、时间或历史通知猜测。
