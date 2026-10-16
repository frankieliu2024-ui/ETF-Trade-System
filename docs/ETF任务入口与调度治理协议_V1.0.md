# ETF任务入口与调度治理协议 V1.0

> 本协议统一三个ETF任务的身份、入口、调度、SKIP、版本保护和业务链路由。
> 本协议不是交易规则、数据规范、Formal Decision业务协议、通知协议或自动维护协议；它不复制这些规范，只负责任务启动前的调度治理和入口接线。

## 1. 适用任务

系统只登记以下三个任务，不新增第三个ETF任务或平行任务：

|任务|自动唤起节点（北京时间）|手动入口|业务主链|
|-|-|-|-|
|ETF系统复核|每日08:30、19:30；周日19:30执行周复核|用户手动启动|System Review|
|ETF交易复盘|每日12:30、20:30；周日20:30执行周复盘|用户手动启动|Trade Review|
|ETF正式决策|交易日09:00、10:00、11:00、13:30、14:30|对话窗口或任务界面手动启动|Formal Decision|

具体是否为交易日、周日、适用市场阶段和节点资格，必须读取current main正式日历、runtime policy和所属业务协议。表内时间不能替代机器事实。

## 2. 入口身份

### ETF系统复核

自动入口沿用既有System Review身份；手动入口沿用既有用户手动执行身份。System Review不得创建或冒充Formal Decision父请求、BDS或Formal Decision事件。

### ETF交易复盘

自动入口和用户手动入口均进入既有Trade Review链。Trade Review不得创建或冒充Formal Decision父请求、BDS或Formal Decision事件。

### ETF正式决策

对话窗口入口固定为：

- source=CHATGPT_MANUAL_FORMAL_ANALYSIS
- entry_mode=MANUAL_CONVERSATION

任务界面手动入口固定为：

- source=CHATGPT_MANUAL_SCHEDULED_FORMAL_ANALYSIS
- entry_mode=MANUAL_SCHEDULED_TASK

系统自动入口固定为：

- source=CHATGPT_SCHEDULED_FORMAL_ANALYSIS
- entry_mode=AUTOMATIC_SCHEDULED_TASK

手动入口不受自动节点时间限制。自动入口仅在current main正式日历允许的交易日和节点执行；不适用时只SKIP，不创建请求、不消费历史请求、不生成正文或报告。

## 3. 启动判定

每次启动必须先：

1. 读取ETF_SYSTEM_INDEX.md；
2. 读取本任务所属业务协议和current main机器配置；
3. 根据实际触发方式建立入口身份；
4. 生成本次新的task_run_id和occurrence_id；
5. 按任务合同判定交易日、周日、market phase和节点适用性；
6. 将任务路由到唯一业务主链。

不得根据任务名称、计划时间、普通行情时间、历史消息或缺失的自动化元数据猜测入口身份。身份不明时不得降级猜测，必须fail-closed或要求重新触发。

## 4. SKIP合同

只有以下情况允许SKIP：

- 自动入口不在current main允许节点；
- 非交易日或所属任务合同明确不适用；
- 周复核不满足周日条件；
- 当前市场阶段与任务合同不匹配。

手动入口不得因为当前时间不是自动节点而SKIP。SKIP不得读取或绑定历史请求、历史报告、历史正文或历史通知。

## 5. 业务链路由

任务调度协议只决定“谁、何时、以什么身份进入哪条链”，不产生业务判断：

- System Review读取System Review协议和owner；
- Trade Review读取Trade Review合同和owner；
- Formal Decision读取Formal Decision执行协议、MASTER、数据规范和通知合同；
- 自动维护只在事实或投影需要维护时读取自动维护协议；
- 通知统一进入既有notification owner，不由任务直接调用PushPlus；
- 三个任务不得互相消费对方的请求、正文、completion、event、REPORT或notification对象。

## 6. 任务Prompt和调度版本保护

每个任务必须维护以下可恢复信息：

- automation_id、conversation_id；
- 时区、timing_mode、调度表达；
- prompt_git_blob_sha、schedule_git_blob_sha；
- 当前启用状态；
- 最近一次已验证版本；
- 回退文件和回退位置。

任何任务Prompt、调度或完成入口变化，必须先保存旧版本完整文本和哈希，再提交新版本；验证失败时只能回退到最近已验证版本，不得凭聊天记忆重写。任务封存文件只用于审计和回退，不构成第二任务或第二规则源。

## 6.1 同任务同正文合同

【ETF系统复核】的自动入口和手动入口必须使用同一份完整System Review正文合同。手动启动只能改变入口身份、运行时间和事实时点，不得缩短章节、减少事实覆盖或改写为仅验证通知链的短报告。

正式任务标题、report_type和业务语义为“ETF系统复核”时，正文至少必须覆盖所属System Review合同要求的系统可用性、市场阶段、关键事实、账户/持仓、运行链状态、用户处理事项、下一阶段、执行状态、置信度和最可能出错的地方。

如果运行目的只是验证completion、REPORT、notification或provider链路，不得使用“ETF系统复核”正式标题、report_type或正式复核event；应作为内部验收证据保留，或使用明确的非正式链路验证标识，且不得冒充完整系统复核。

禁止使用“MANUAL_SYSTEM_REVIEW_CHAIN_VALIDATION”作为ETF_SYSTEM_REVIEW正式报告的正文缩减理由。任何同任务不同正文合同都必须先升级任务分类和协议，再实施。

## 6.2 Scheduled Review后置交接合同

System Review和Trade Review在生成唯一完整FINAL_CONTENT后，不得以“正文已生成”作为任务完成。必须在同一运行中提交并回读唯一的`SCHEDULED_REVIEW_COMPLETION`，并继续形成canonical review event、REPORT、notification_id和provider result。任一对象缺失、错绑或回读失败，任务状态必须保持未闭环；不得补绑历史对象、补发历史正文或创建第二请求、第二event、第二通知。provider success只能记录为provider接口成功，不得表述为用户已收到。

## 7. 统一完成边界

调度成功、任务已启动、请求已写入、正文已生成、workflow success、REPORT生成、provider success和用户已收到必须分别记录。

任务只有在其所属业务协议规定的完成条件满足后，才能表述为完成：

- System Review按Scheduled Review合同；
- Trade Review按Trade Review合同；
- Formal Decision按唯一FINAL_CONTENT、BDS、completion、canonical event、REPORT、notification和provider合同；
- provider success不得表述为用户已收到。

## 8. 禁止事项

- 不新增第四个ETF任务；
- 不用任务名称推断入口身份；
- 不把手动启动误判为自动节点；
- 不把自动非适用节点强行转换为手动执行；
- 不跨任务复用身份、正文、completion、event、REPORT或notification；
- 不因任务失败重放历史正文或创建第二请求；
- 不通过修改任务时间绕过业务SKIP；
- 不自动执行证券交易、调仓或恢复交易权限；
- 不修改MASTER、provider priority、正式监测范围或无关业务链。

## 9. 验收

每次任务治理变更至少验证：

1. 三个任务的身份、时区、调度和主链映射一致；
2. Formal Decision五个自动节点与current main登记一致；
3. System Review和Trade Review的交易日/周日边界一致；
4. 手动入口在非自动节点不SKIP；
5. 自动入口在非适用节点只SKIP；
6. 入口身份不明时不猜测；
7. 任务版本可回读、可回退；
8. 任务不会跨链消费其他任务对象；
9. 业务完成和通知完成仍按所属协议分别判断；
10. 不触及MASTER、交易权限、provider priority和无关业务链。

## 10. 规则归属

节点、入口枚举和任务身份由本协议登记；交易日历、market phase、数据资格、业务判断、正文、完成事件、通知和生产变更分别由所属唯一规范负责。若本协议与current main机器事实或所属业务协议冲突，必须修复协议镜像或实现，不得由任务Prompt自行覆盖。
