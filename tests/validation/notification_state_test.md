# 旧通知场景回放测试

场景：10:30 节点已存在，用户未处理；11:30 节点生成；用户在 11:40 进入旧的 10:30 通知。

预期：系统读取 `data/state/CURRENT.json`，以 `latest_valid_node=11:30` 为当前有效市场状态；10:30 只保留在 `superseded_nodes` 历史中，不继续作为当前状态。该状态层不生成交易动作。

自动测试：`python -m unittest tests.test_state_layer`
