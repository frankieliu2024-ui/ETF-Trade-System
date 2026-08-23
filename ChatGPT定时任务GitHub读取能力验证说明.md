# ChatGPT 定时任务 GitHub 读取能力验证说明

## 目的

本说明只准备 ChatGPT Scheduled Tasks 的只读验证入口，不实现任务、不发送通知、不执行交易。

## 需要验证的内容

1. ChatGPT Scheduled Tasks 是否可以访问 GitHub 私有仓库 `frankieliu2024-ui/ETF-Trade-System`。
2. 是否可以读取 `data/state/CURRENT.json`。
3. 是否可以读取 `query_context.json`。
4. 是否可以读取 `post_market_review/post_market_review_event.json`。
5. 是否能识别 `latest_commit` 与上下文中的规则版本，而不假设本地缓存为最新。

验证提示文件：`chatgpt_task_probe.json`。该文件只提供仓库名、路径、最新提交探针和更新时间；其中 `access_verified=false` 是有意保留的事实状态，Codex 不假设 ChatGPT 任务拥有私有仓库权限。

## 预期读取边界

定时任务只能读取状态和事件，不能修改 MASTER、Dashboard、经验库、行情档案或账户事实，也不能从上下文生成买卖动作、金额或订单。

## 失败时备用方案

- 由用户主动进入 ETF 项目聊天，读取 `query_context.json` 或上传当前文件内容。
- 由 Codex/GitHub Actions 生成只读上下文，再由用户在 ChatGPT 中主动查询。
- 若未来建立受控中转服务，只允许最小字段、只读权限和版本校验，不把 GitHub 私有仓库凭据交给聊天任务。

## 当前结论

截至本文件生成时，没有验证 ChatGPT Scheduled Tasks 可以读取该 GitHub 私有仓库；这项能力必须在目标 ChatGPT 工作区中单独实测。该文件不构成权限证明，也不代表原生通知已实现。
