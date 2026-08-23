# ChatGPT 交互入口链测试

本文件记录的是状态层入口测试，不执行交易判断、不生成金额、不连接券商。

## 场景 1：旧通知进入

- 固定事实：10:30 节点已存在，随后 11:30 节点生成。
- 入口行为：读取 `CURRENT.json`，不使用通知携带的旧状态。
- 预期与结果：当前有效节点为 `11:30`，通过。

## 场景 2：账户截图入口

- 接口文件：`data/state/account_fact.json`。
- 当前状态：`MISSING`，字段为 null 或空数组，没有模拟账户事实。
- 入口行为：只有账户事实状态为 `VALID` 才允许进入人工正式决策准备；本阶段不自动生成金额或动作。
- 结果：保护条件通过；本次未上传真实券商截图。

## 场景 3：无账户截图

- `data/state/decision_context.json.account_fact_status=MISSING`。
- `data/state/decision_context.json.needs_account_screenshot=true`。
- 结果：明确要求账户截图；不生成正式金额、买入、卖出或订单，通过。

## 结论

入口可以识别最新状态并暴露账户事实缺口，但正式交易判断仍属于人工 ChatGPT 交互边界。本测试不代表正式上线许可。
