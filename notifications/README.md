# 通知实现目录

主动通知的唯一人类可读运行规范为：

`docs/ETF主动通知体系.md`

本目录只承担通知实现与运行文件的技术组织，不再复制通知条件、阈值、标题体系、固定总结时点、去重策略或Point-in-Time边界，避免形成第二份可能漂移的规范。

当前主要实现入口：

- 统一通知状态：`data/state/notification_center.json`
- 正式决策、账户、成交、系统事件：`scripts/notification_center.py`
- 市场价值事件：`scripts/send_market_shock_notification.py`
- A股/亚太节点总结：`scripts/send_regional_session_summary.py`
- 美股现金盘节点总结：`scripts/send_us_session_summary.py`
- 公共通知模板：`scripts/market_notification_common.py`
- A股通知workflow：`.github/workflows/decision-notification.yml`
- 亚太行情与通知workflow：`.github/workflows/overseas-preopen-pulse.yml`
- 美股行情与通知workflow：`.github/workflows/us-extended-hours-pulse.yml`

任何会改变通知对象、触发条件、注意力阈值、一级标题、固定总结节点、去重/冷却、Point-in-Time边界或PushPlus模板的生产修改，都必须同步更新 `docs/ETF主动通知体系.md` 并执行系统一致性检查。
