# Codex协作执行说明

本文只定义 ChatGPT、Codex 与 GitHub 之间的可恢复任务交接方式，不定义交易规则、数据规则、通知规则或生产变更准入。生产变更仍以《生产变更与并发写入协议 V1.8》为唯一规范来源。

## 1. 任务入口

当一项维护事项已在现行生产治理下具备执行资格，并适合交给 Codex 时，ChatGPT 将完整、可独立执行的 `CODEX_EXECUTION_BRIEF` 写入对应 GitHub Issue。用户默认只需启动 Codex，例如：

`执行 frankieliu2024-ui/ETF-Trade-System #<issue> 中最新 CODEX_EXECUTION_BRIEF，完整授权执行。`

用户无需重复搬运长 Prompt。

## 2. 执行与回读

Codex 必须从执行时 latest `main` 和 `ETF_SYSTEM_INDEX.md` 起跑，读取所属规范与当前事实，先判断准入、canonical owner、根因和范围，再按生产治理执行隔离实现、测试、PR、CI 及必要的 latest-main / merged-main 验收。

Codex 的结果必须回写同一 Issue 或关联 PR，至少包括实际修改、commit、测试、CI、合并状态、验收结果、残余风险和是否建议合并。ChatGPT 与【ETF变更复核】后续直接从 GitHub 读取 Issue、PR、CI 和 latest main；聊天记录不是唯一交接媒介。

## 3. 边界

Issue 中的完整 brief 是执行输入，Issue/PR 中的结构化结果是执行输出；二者都必须可由 GitHub 恢复。聊天中主动转发的内容可以作为补充，但不能替代 GitHub 正式记录。

生产合并、Issue关闭和组合验收仍严格服从 V1.8；本说明不授予自动合并权限，不降低任何测试、writer ownership、latest-main、PIT 或 E2E要求。不得因此新增 Task-to-Task 通信、共享聊天状态、平行 workflow、state、writer 或第二治理规则源。
