# 通知实现目录

主动通知域的唯一规范性规则来源为：

`docs/ETF主动通知体系.md`

本目录只承担通知实现与运行文件的技术组织，不复制通知资格、阈值语义、标题体系、固定总结时点、去重策略、可比事实门禁或Point-in-Time边界，避免形成第二份可能漂移的规则。运行事实由当前 `main` 的脚本、配置和状态记录；规则解释以 `docs/ETF主动通知体系.md` 为准，若实现与规范冲突应修复实现，不得反向覆盖规范。

当前主要实现入口：

- 统一通知状态：`data/state/notification_center.json`
- 统一变化证据门：`scripts/notification_materiality_guard.py`
- 正式生产通知受保护入口：`scripts/run_guarded_notification.py`
- 正式决策、账户、成交、系统事件：`scripts/notification_center.py`
- 市场价值事件：`scripts/send_market_shock_notification.py`
- A股/亚太节点总结：`scripts/send_regional_session_summary.py`
- 美股现金盘节点总结：`scripts/send_us_session_summary.py`
- 公共通知模板：`scripts/market_notification_common.py`
- A股通知workflow：`.github/workflows/decision-notification.yml`
- 亚太行情与通知workflow：`.github/workflows/overseas-preopen-pulse.yml`
- 美股行情与通知workflow：`.github/workflows/us-extended-hours-pulse.yml`

任何会改变通知对象、触发条件、注意力阈值、一级标题、固定总结节点、可比事实门禁、去重/冷却、Point-in-Time边界或PushPlus模板的生产修改，都必须先或同时更新 `docs/ETF主动通知体系.md` 并执行系统一致性检查。新增独立PushPlus生产入口属于架构变化，必须同步纳入统一规则和一致性测试，不得绕过受保护入口。
