# Codex协作执行说明

## V1.9 治理减法接口

生产变更治理以 `docs/生产变更与并发写入协议_V1.0.md` V1.9 和其机器镜像为唯一来源。本说明只保存执行器交接字段，不复制稳定治理正文。

执行前先按机器镜像对变更进行 Tier 0–3 分类；无法判定时取 Tier 3。Issue/BRIEF/PACKET 只引用 `protocol_version`、`risk_tier`、scope、canonical owner、证据身份和本轮结果，不复制协议条款。

- Tier 0：文档、Prompt、展示、只读审计、研究或artifact-only；不进入生产mutation。
- Tier 1：单owner、确定性且不改变canonical writer/state/PIT/account/trade/authority/topology；只执行其最小测试、latest-main semantic check与CI。
- Tier 2：canonical producer/state、通知状态或重要runtime contract；按机器矩阵执行owner check、candidate及必要merged-main验收。
- Tier 3：账户、成交、PIT/freshness、writer ownership、MASTER/交易权限、并发拓扑或不可逆行为；执行完整矩阵及必要真实production exposure。

latest-main仍是硬要求；只有共享owner/文件冲突、行为语义变化或候选证据失效才重建successor。动态state-only main前进可复用合法 `acceptance_evidence_identity`。global hard FAIL始终保留；change-specific acceptance单独判定，可靠无关失败不自动升级低/中风险事项。root-cause-complete在已证明failure domain与相邻断点的枚举矩阵完成后停止；独立发现另行准入。


本文只定义 ChatGPT、执行器与 GitHub 之间的可恢复任务交接方式，不定义交易规则、数据规则、通知规则或生产变更准入。`CODEX_EXECUTION_BRIEF`／`CODEX_EXECUTION_PACKET`是可由不同执行器消费的执行控制面，不因名称含有“Codex”而限定执行器；生产变更仍以《生产变更与并发写入协议 V1.8》为唯一规范来源。

## 1. 任务入口

当一项事项需要可恢复、可独立执行的任务交接时，ChatGPT 负责把控制面写入对应 GitHub Issue，并按任务复杂度、当前工具能力、工程风险和用户操作成本选择执行器。用户默认不需要重复搬运长 Prompt。

### 1.1 简单、确定性任务

对于根因、范围、canonical owner 和验证路径已经明确，且不依赖多轮纠偏或跨 Issue 证据的简单任务，使用 `CODEX_EXECUTION_BRIEF` 即可；BRIEF只描述可恢复执行控制面，不规定必须由哪一种执行器运行。用户可按第1.3节选择执行器后直接启动：

`执行 frankieliu2024-ui/ETF-Trade-System #<issue> 中最新 CODEX_EXECUTION_BRIEF，完整授权执行。`

“完整授权执行”默认表示：在本事项已准入的 scope 与 canonical owner 不变、候选测试/CI/latest-main 检查通过、无未解决 review blocker、无本次变更新增或无法归因的 hard FAIL，且最新 main 推进已被吸收或按治理规则分类为非阻塞时，执行器可使用当前 PR head 原子合并，并继续完成 merged-main deterministic acceptance、failure attribution、正式结果持久化和可关闭则关闭。该授权不允许 force push，也不把 candidate green 等同于生产闭环。用户明确写出“不得合并”“不自动合并”或等价限制时，以更窄限制为准；若出现 scope 扩大、新独立根因、hard/unattributed FAIL，或关闭依赖未来真实生产暴露，必须停止并回到用户。

### 1.2 复杂任务与多轮纠偏任务

对于专项研究、复杂根因分析、多脚本／workflow 联动、多轮纠偏、跨 Issue 复用证据、容易受旧评论干扰，或结论可能影响生产架构／正式决策消费的任务，必须使用单一 `CODEX_EXECUTION_PACKET` 作为本轮唯一执行控制面；PACKET的命名不意味着只能由Codex执行。

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

只要任务需要用户启动或转交执行器，除了把完整 `CODEX_EXECUTION_BRIEF` 或 `CODEX_EXECUTION_PACKET` 写入 GitHub Issue 外，**还必须在同一条 ChatGPT 对话回复中明确给用户一段可直接复制的启动指令，并标明建议执行器**。不得只告诉用户“已写入 Issue”或“下一步交给某执行器”而省略这段用户侧可复制文本。

用户侧启动指令应尽量短，只负责准确指向 GitHub 中的唯一执行控制面并标明执行器，不重复搬运完整长 Prompt：

- 简单任务（建议执行器：`CHATGPT_CHAT` 或 `CHATGPT_WORK`，复杂工程任务可用 `CODEX`）：`建议由<执行器>执行 frankieliu2024-ui/ETF-Trade-System #<issue> 中最新 CODEX_EXECUTION_BRIEF，完整授权执行。`
- 复杂任务（建议执行器：`CODEX`；若 ChatGPT Work 已具备完整能力也可直接执行）：`建议由<执行器>执行 frankieliu2024-ui/ETF-Trade-System #<issue> comment <comment_id> 中的 CODEX_EXECUTION_PACKET，完整授权执行。该评论是本轮唯一执行控制面。`

如果本轮还存在必须强调的执行边界，例如“只读研究”“不得合并”“等待前序集成”“必须复用某个历史 comment”，ChatGPT 可在上述短启动指令后追加一小句必要限定；但不得把 Issue 内完整 packet 再次复制到聊天中。用户无需自行从 Issue 中总结或改写 Codex 指令。

### 1.4 GitHub Issue 写入纪律

Chat 中的探索、脑暴、方案比较、临时假设和尚未收敛的优化建议默认不写入 GitHub。`@GitHub` 默认表示基于 current main 与正式 GitHub 事实工作，不自动表示授权把当前讨论写入 Issue。

ChatGPT 可在无需逐次人工确认的情况下，对已有正式事项执行低频、必要的 GitHub 持久化，但仅限以下内容：

- 新增且已确认、会改变当前事项状态或后续执行的生产事实；
- `ADMISSION`、`INTEGRATION`、closure condition 等治理状态的实质变化；
- 真正进入独立执行阶段所需的正式 `CODEX_EXECUTION_BRIEF`／`CODEX_EXECUTION_PACKET`；
- 代码、PR、CI、latest-main replay、merged-main 验收、failure attribution 等正式执行或验证结果；
- 已经收敛、且不持久化将导致后续执行器无法可靠恢复的正式结论。

没有实质 delta 时，不新增 Issue comment。若变化只是当前状态更新，优先维护 Issue 正文或当前 control surface，不通过连续评论累积重复状态。只有需要保留独立审计事件、正式执行控制面或结构化执行结果时才新增 comment。

新观点、新方案、新架构、scope 扩大或新的独立 Issue 默认先在 Chat 中收敛，并继续服从当前生产治理与 `NEW_WORK_ITEM_GATE`；不得因“可能有用”或用户仅使用 `@GitHub` 就提前正式化。复杂事项进入执行后，仍以一个明确 comment ID 的 `CODEX_EXECUTION_PACKET` 作为本轮唯一执行控制面；未被引用的旧评论仅作历史证据。

这套纪律的目标是保持 GitHub 可恢复而不过度记录：**正式事实和正式执行自动落库；探索和讨论默认留在 Chat。**

## 2. 执行前置校验

Codex 每次执行必须从执行时 latest `main` 和 `ETF_SYSTEM_INDEX.md` 起跑，并先输出 `LATEST_MAIN_AT_START`。如果本地或任务上下文中的起点不是执行时 GitHub `main` HEAD，必须先重新同步；不得在旧 SHA 上继续执行并把结果称为 latest-main 结论。

随后按 INDEX 路由读取所属规范、当前事实、production mutation protocol、canonical owner 及本轮最小充分依赖，再判断准入、根因、范围和实现路径。

创建新的BRIEF/PACKET Issue前，必须先按当前生产治理SSOT完成NEW_WORK_ITEM_GATE，并在ChatGPT对话中给出立项判断和可直接复制的Codex短启动文本。

如果 `CODEX_EXECUTION_PACKET` 明确引用其他 Issue、PR、comment、commit、历史恢复能力或既有成功验证，选定执行器必须实际读取并复用这些证据；不能只读取当前 Issue 的局部目录或当前工作树后自行推断“能力不存在”。

## 3. 执行与回读

选定执行器按现行生产治理执行只读研究、隔离实现、测试、PR、CI、latest-main replay 及必要的 merged-main 验收； merged-main 失败时，必须回读当前生产治理 SSOT 的 failure attribution 与 Issue closure contract，分别报告全局结果、change-specific acceptance、归因类别和当前 Issue 是否可闭环；哪些步骤允许执行、何时允许集成，仍由《生产变更与并发写入协议 V1.8》决定。

### 3.1 确定性生产集成闭环（ChatGPT编排）

当用户已经明确授权当前事项进入集成（包括满足上述条件的“完整授权执行”），且最终人工复核、latest-main、candidate acceptance、CI 与现行生产治理均允许合并时，如果 merged-main 剩余验收只依赖现有 GitHub Actions／确定性检查，而**不依赖未来真实市场、真实通知或其他必须等待的生产暴露**，ChatGPT 应把下列步骤作为同一次集成闭环连续完成：

`最终人工复核 → 最后一次 latest main／PR head 确认 → 合并 → 读取该稳定 mutation SHA 的 main-side workflow → full consistency／maintenance／账户成交勾稽／E2E → failure attribution → 回写 Issue／PR → 满足关闭条件则关闭 → 一次性向用户报告最终结果`

这里的“同一次闭环”只收紧 ChatGPT 的执行编排，不合并或删除治理阶段。candidate acceptance 仍不能替代 merged-main acceptance；ChatGPT 不得因为候选 CI 已绿而预先宣布生产通过。

若 main-side workflow 已启动且预计可在当前执行中取得确定结果，ChatGPT 不应仅因其暂时为 `queued`／`in_progress` 就提前把“等待复核”重新交给用户；应优先继续读取该 run 至形成可判定结果，再完成归因和关闭判断。现有 workflow 在验收后产生的 state-only 持久化提交不要求递归等待新的同类验收；change-specific acceptance 绑定到实际稳定 mutation SHA，并按现行 failure attribution 判断后续动态状态。

只有当关闭条件本身确实需要下一次真实市场节点、真实 Scheduled Actor、真实通知投递或其他未来生产事实时，才结束本次确定性闭环并明确进入 `OBSERVE`。此时必须写出具体、可证伪的释放条件和下一次有信息价值的真实暴露窗口，不得泛化为“合并后再等等”或“以后再复核”。

本节不得用于新增第二 workflow、第二验收器、Task-to-Task通信、等待状态库或自动合并权限；优先复用当前 main 已有的 CI、production acceptance、Issue／PR 和正式运行事实。是否允许合并、何时允许关闭以及 failure attribution 仍完全服从当前生产治理 SSOT。

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

### 3.2 单一集成状态机与证据复用

同一根因事项必须复用同一个 Issue 控制面，并且在任何 replay、rebase 或替代 PR 决策前调用现有 `scripts/semantic_latest_main.py` 的 `integration_action`：

- `NO_REPLAY_REQUIRED`：main 相对 PR base 只有动态运行事实或没有变化；保留现有 PR head 和已通过的 candidate 证据，不创建替代 PR，不重复完整 candidate suite。
- `REVIEW_MAIN_DELTA`：main 含正式事实或 request 变化；只检查该 delta 是否与当前 stable diff 交互，未证明交互前不得自动 replay，也不得把它当作无变化。
- `REPLAY_REQUIRED`：main 含 stable production change 或 UNKNOWN 变化；才允许从 latest main 重新隔离重放。

candidate 证据的复用键是：PR head SHA、stable semantic diff 和当前生产治理/合同版本。PR head 未变化且分类为 `NO_REPLAY_REQUIRED` 时，动态 main 提交不使既有 candidate 证据失效；GitHub 若只要求分支技术上追平，优先执行最低成本的 transport 更新，不能借此重建变更或重复无关验收。任何证据复用都不替代最终 merged-main acceptance。

一个 Issue 同时只保留一个 active integration PR。只有 `REPLAY_REQUIRED`、实际 stable/formal interaction，或旧 PR 的 GitHub transport 已不可用时，才允许创建 successor PR；创建 successor 后应关闭旧 PR/分支，避免多个候选继续触发 CI。state-only 的通知、诊断或验收持久化提交按现有 `scripts/acceptance_scope.py` 分类为非递归 acceptance 输入，不重新启动同一变更的 candidate/production acceptance。

一旦用户明确授权合并，且 scope、review、latest-main、candidate、CI 与治理门全部满足，执行器必须在同一执行 episode 内继续读取 merged-main deterministic workflow 到终态，完成 change-specific acceptance、failure attribution、Issue/PR 回写和可关闭判断；不能因 run 暂时 `queued`／`in_progress` 就把确定性收尾交回用户。若归因为 `PREEXISTING_UNRELATED` 或 `NEW_UNRELATED_DISCOVERY`，保持全局失败可见但不得重启当前变更；只有 `INTRODUCED_BY_CURRENT_CHANGE` 或 `ATTRIBUTION_INCONCLUSIVE` 阻塞当前闭环。

本节是现有协作入口的执行合同，不新增 workflow、state store、merge queue、approval bot、第二 classifier 或第二 acceptance engine；合并授权、安全边界、writer ownership、PIT/freshness、system consistency 和 E2E 仍完全服从 V1.8。

## 4. 选择规则

默认使用以下分工：

- `CHATGPT_CHAT`：适合范围清楚、低复杂度、可安全完整执行的任务；
- `CHATGPT_WORK`：适合可在当前Work能力内完整完成读取、实现、测试、PR、CI／验收的任务，尤其可降低用户切换设备或入口的成本；
- `CODEX`：优先用于大范围代码搜索、复杂重构、多脚本／workflow联动、深度terminal／本地repo依赖、完整复杂回归等明显更适配的工程任务；
- BRIEF／PACKET分别由任务复杂度决定，不由执行器决定；复杂任务仍使用明确 comment ID 的 `CODEX_EXECUTION_PACKET`；
- 如果简单任务在执行中出现两次以上方向性误解、旧版本起跑、跨证据遗漏或同一根因反复返工，应升级为 `CODEX_EXECUTION_PACKET`，不继续追加模糊“最新 Brief”。

ChatGPT 负责判断任务复杂度并选择执行器与交接模式；ChatGPT Chat或Work只要能够安全、完整完成任务即可直接执行，不强制转Codex。用户不需要自行维护两套协作规则，也不需要自行从 Issue 中提炼启动文本。便利性不得覆盖工程质量或能力边界。

## 5. 边界

Issue 中的完整 brief／packet 是执行输入，Issue／PR 中的结构化结果是执行输出；二者都必须可由 GitHub 恢复。聊天中主动转发的内容可以作为补充，但不能替代 GitHub 正式记录。

生产合并、Issue 关闭和组合验收仍严格服从 V1.8；本说明不授予自动合并权限（无条件合并、绕过 review 或跳过验收的权限），但“完整授权执行”在本说明第3.1节确定性门全部满足时构成条件性合并授权；不降低任何测试、writer ownership、latest-main、PIT、system consistency 或 E2E 要求。

不得因此新增 Task-to-Task 通信、共享聊天状态、平行 workflow、state、writer、provider、decision engine 或第二治理规则源。`CODEX_EXECUTION_PACKET` 只提高执行确定性，不改变交易权限、研究转化标准或生产准入语义。
