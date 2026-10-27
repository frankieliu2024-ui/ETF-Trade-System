# ETF三类任务当前冻结指针（live Prompt authority）

本目录只保留一个当前指针：`ETF_THREE_TASKS_CURRENT_FREEZE.json`。三类任务在 ChatGPT 定时任务界面保存的 live Prompt 是唯一运行指令；current main 中的文件仅用于审计、测试、生产协议映射和回退登记，不得覆盖 live Prompt。

读取顺序：

1. 先读取 `ETF_SYSTEM_INDEX.md`；
2. 再读取本清单；
3. 按 `task_id` 选择唯一任务记录；
4. 按入口身份和运行身份选择本次对象；
5. 按 `content_hash` 绑定 FINAL_CONTENT、completion、event、REPORT、notification 和 provider result；
6. 若任一层缺失，按清单中的失败分类停止，不使用历史对象补绑。

本版本明确区分：

- 仓库合同已冻结；
- ChatGPT 平台live Prompt、Schedule及其可验证hash尚待逐任务回读；
- 历史封存文件只能审计和回退，不能作为运行时规则；
- 该清单不新增任务、workflow、producer、checker、state或writer。

当前冻结登记只有在三类任务的 live Prompt 和 Schedule 已通过云浏览器回读后才可更新；平台不提供原生 Prompt/Schedule hash 时，不得伪造或推算 hash。仓库冻结文件只记录回读事实和审计指针，不构成第二套运行指令。
