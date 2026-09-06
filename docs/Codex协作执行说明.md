# Codex协作执行说明

本文只定义 ChatGPT、Codex 与 GitHub 之间的可恢复任务交接方式，不定义交易规则、数据规则、通知规则或生产变更准入。生产变更仍以《生产变更与并发写入协议 V1.8》为唯一规范来源。

## 1. 任务入口

当一项事项适合交给 Codex 时，ChatGPT 负责把可恢复、可独立执行的任务指令写入对应 GitHub Issue。用户默认不需要重复搬运长 Prompt。

### 1.1 简单、确定性任务

对于根因、范围、canonical owner 和验证路径已经明确，且不依赖多轮纠偏或跨 Issue 证据的简单任务，使用 `CODEX_EXECUTION_BRIEF` 即可。用户可直接启动：

`执行 frankieliu2024-ui/ETF-Trade-System #<issue> 中最新 CODEX_EXECUTION_BRIEF，完整授权执行。`

### 1.2 复杂任务与多轮纠偏任务

对于专项研究、复杂根因分析、多脚本／workflow 联动、多轮纠偏、跨 Issue 复用证据、容易受旧评论干扰，或结论可能影响生产架构／正式决策消费的任务，必须使用单一 `CODEX_EXECUTION_PACKET` 作为本轮唯一执行控制面。

`CODEX_EXECUTION_PACKET` 必须写入一个明确的 Issue comment，并至少包含：

- 本轮唯一有效执行 comment ID；
- 执行时 latest `main` 校验要求；
- `ETF_SYSTEM_INDEX.md` first 的读取要求；
- 本轮目标、范围、禁止事项和必须输出；
- 必须复用的历史 Issue／PR／comment／已验证路径及其具体 ID；
- 对旧评论的优先级边界：未被当前 packet 明确引用的旧 Brief、旧结论和旧准入只作历史证据，不作为并列执行指令；
- 当本轮结果与已验证历史事实冲突时，必须先解释环境、入口、版本或数据路径差异，不得直接用新失败覆盖既有成功事实；
- 不得因单一子问题 `INCONCLUSIVE`、样本不足或局部数据缺失而提前终止仍可继续完成的其他独立子任务。

用户启动时应引用明确 comment ID，而不是只说“最新评论”或“最新 Brief”，例如：

`执行 frankieliu2024-ui/ETF-Trade-System #<issue> comment <comment_id> 中的 CODEX_EXECUTION_PACKET，完整授权执行。该评论是本轮唯一执行控制面。`

复杂任务仍以 GitHub Issue 作为长期记录和恢复入口；`CODEX_EXECUTION_PACKET` 只是本轮执行控制面，不新增第二治理规则源。

### 1.3 ChatGPT 对话中的用户启动指令

只要 ChatGPT 判断“该任务更适合交给 Codex 执行”，除了把完整 `CODEX_EXECUTION_BRIEF` 或 `CODEX_EXECUTION_PACKET` 写入 GitHub Issue 外，**还必须在同一条 ChatGPT 对话回复中明确给用户一段可直接复制发送给 Codex 的短启动指令**。不得只告诉用户“已写入 Issue”“请执行最新 Brief”而省略这段用户侧可复制文本。

用户侧启动指令应尽量短，只负责准确指向 GitHub 中的唯一执行控制面，不重复搬运完整长 Prompt：

- 简单任务：`执行 frankieliu2024-ui/ETF-Trade-System #<issue> 中最新 CODEX_EXECUTION_BRIEF，完整授权执行。`
- 复杂任务：`执行 frankieliu2024-ui/ETF-Trade-System #<issue> comment <comment_id> 中的 CODEX_EXECUTION_PACKET，完整授权执行。该评论是本轮唯一执行控制面。`

如果本轮还存在必须强调的执行边界，例如“只读研究”“不得合并”“等待前序集成”“必须复用某个历史 comment”，ChatGPT 可在上述短启动指令后追加一小句必要限定；但不得把 Issue 内完整 packet 再次复制到聊天中。用户无需自行从 Issue 中总结或改写 Codex 指令。

## 2. 执行前置校验

Codex 每次执行必须从执行时 latest `main` 和 `ETF_SYSTEM_INDEX.md` 起跑，并先输出 `LATEST_MAIN_AT_START`。如果本地或任务上下文中的起点不是执行时 GitHub `main` HEAD，必须先重新同步；不得在旧 SHA 上继续执行并把结果称为 latest-main 结论。

随后按 INDEX 路由读取所属规范、当前事实、production mutation protocol、canonical owner 及本轮最小充分依赖，再判断准入、根因、范围和实现路径。

如果 `CODEX_EXECUTION_PACKET` 明确引用其他 Issue、PR、comment、commit、历史恢复能力或既有成功验证，Codex 必须实际读取并复用这些证据；不能只读取当前 Issue 的局部目录或当前工作树后自行推断“能力不存在”。

## 3. 执行与回读

Codex 按现行生产治理执行只读研究、隔离实现、测试、PR、CI、latest-main replay 及必要的 merged-main 验收；哪些步骤允许执行、何时允许集成，仍由《生产变更与并发写入协议 V1.8》决定。

Codex 的结果必须回写同一 Issue 或关联 PR，至少包括：

- 实际读取的 latest-main SHA；
- 实际修改和 canonical owner；
- commit／branch／PR；
- 定向测试、完整测试及已知 baseline 异常；
- CI 状态；
- 是否已合并；
- merged-main 验收状态；
- 残余风险；
- `ADMISSION`、必要时的 `INTEGRATION`；
- 是否建议合并／关闭；
- 仍需等待的事实及其可证伪解除条件。

如果执行结果与 packet 中引用的既有成功事实冲突，结果回写必须显式说明冲突来源和差异，不能把“本次未复现”写成“历史能力从未存在”。

ChatGPT 与【ETF变更复核】后续直接从 GitHub 读取 Issue、PR、CI 和 latest main；聊天记录不是唯一交接媒介。

## 4. 选择规则

默认使用以下分工：

- 简单、确定性、单一根因任务：`CODEX_EXECUTION_BRIEF`；
- 复杂研究、多轮纠偏、跨 Issue 依赖、容易被历史评论污染的任务：明确 comment ID 的 `CODEX_EXECUTION_PACKET`；
- 如果简单任务在执行中出现两次以上方向性误解、旧版本起跑、跨证据遗漏或同一根因反复返工，应升级为 `CODEX_EXECUTION_PACKET`，不继续追加模糊“最新 Brief”。

ChatGPT 负责判断任务复杂度并选择交接模式；用户不需要自行维护两套协作规则，也不需要自行从 Issue 中提炼 Codex 启动文本。

## 5. 边界

Issue 中的完整 brief／packet 是执行输入，Issue／PR 中的结构化结果是执行输出；二者都必须可由 GitHub 恢复。聊天中主动转发的内容可以作为补充，但不能替代 GitHub 正式记录。

生产合并、Issue 关闭和组合验收仍严格服从 V1.8；本说明不授予自动合并权限，不降低任何测试、writer ownership、latest-main、PIT、system consistency 或 E2E 要求。

不得因此新增 Task-to-Task 通信、共享聊天状态、平行 workflow、state、writer、provider、decision engine 或第二治理规则源。`CODEX_EXECUTION_PACKET` 只提高执行确定性，不改变交易权限、研究转化标准或生产准入语义。
