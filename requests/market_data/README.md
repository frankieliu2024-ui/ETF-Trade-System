# 按需行情请求入口

本目录是 ChatGPT / Codex / 人工维护端触发同花顺行情查询的稳定入口。新增 JSON 请求文件即触发 `.github/workflows/on-demand-market-data.yml`；不依赖 `workflow_dispatch`，不修改正式 ETF 研究池、账户持仓或四个正式文件。

## 单日或区间个股历史

```json
{
  "request_id": "smic_688981_20260821",
  "asset_type": "stock",
  "mode": "history",
  "code": "688981",
  "start_date": "2026-08-21",
  "end_date": "2026-08-21",
  "adjust": "none"
}
```

## ETF 历史

```json
{
  "request_id": "etf_561980_20260101_20260821",
  "asset_type": "etf",
  "mode": "history",
  "code": "561980",
  "start_date": "2026-01-01",
  "end_date": "2026-08-21"
}
```

## 最新快照

将 `mode` 设为 `snapshot`，只需 `request_id / asset_type / code / mode`。

## 结果

- 摘要与质量状态：`data/market/on_demand/results/<request_id>.json`
- 历史标准化数据：`data/market/on_demand/datasets/<request_id>.csv`
- 历史字段固定为：`date, open, high, low, close, volume, amount`
- 个股长历史按不超过 365 天窗口分段请求并去重；ETF 长历史使用已验收的 `hithink-finance fund history` CLI 分段获取。
- 结果必须 `ok=true` 才可进入正式分析或专项回测；失败结果保留 error，不得用第三方数据静默替代。

## 使用边界

按需行情是数据服务，不产生买卖动作。专项回测可以直接读取 datasets CSV，并在研究输出中记录 request_id、数据区间、来源和质量状态。请求文件保留为审计记录；相同 request_id 已有结果时不会重复拉取。
