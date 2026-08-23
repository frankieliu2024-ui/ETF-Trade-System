# 盘后维护流程

云 Runner 继续运行所有行情节点，但只有 `latest_valid_node` 为 `1500` 或 `close` 时，`scripts/build_post_market_review.py` 才生成 `post_market_review_event.json`。盘后事件用于提醒用户进入复盘流程，不是交易建议。

账户事实必须来自用户上传的 `BROKER_SCREENSHOT`。账户事实为 `MISSING` 时，事件状态为 `WAITING_USER`，`review_context.json` 不生成正式复盘结论。

