# ETF冻结历史归档

本目录只保存已退役的冻结正文、历史快照、旧注册表和已退役方案。它不是当前规则源，也不是当前冻结入口。

当前唯一冻结入口：

`docs/freezes/ETF_THREE_TASKS_CURRENT_FREEZE.json`

当前有效冻结包由该指针的 `bundle_path` 指定。恢复或审计时，必须先读取 `ETF_SYSTEM_INDEX.md` 和当前指针，再按当前指针登记的 MANIFEST、证据索引和恢复手册执行。

本目录中的文件只能用于历史审计、差异比较和人工回退依据，不得被任务执行器、通知链、恢复器或自动维护流程当作当前 Prompt、当前 Schedule、当前业务规则或当前运行对象。

本次归档迁移：

- `docs/ETF三类任务当前版本冻结封存_2026-10-16.md` → `20261016/`
- `docs/ETF三类任务统一快照封存_2026-10-17.md` → `20261017/`
- `docs/formal-decision-scheduled-task-rebuild-v2.md` → `retired/`
- `docs/automation_prompt_history/ETF三类任务冻结注册表.json` → `registries/`

正式协议、技术说明、测试和运行代码不属于本目录的历史冻结材料，不得因文件名含有“协议”“治理”或“说明”而迁入或删除。
