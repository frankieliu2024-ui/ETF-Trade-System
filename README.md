# ETF Trade System

This repository is the cloud runtime and canonical state repository for the ETF trading system. It collects and validates market snapshots, maintains runtime contexts, preserves auditable account/trade/research state, and does not place orders.

## Canonical entry points

- Trading rules and the **current formal system version**: [`ETF规则_MASTER.md`](./ETF规则_MASTER.md). The README does not duplicate a hard-coded V2.x version; the MASTER title is the only formal version source.
- Repository map and formal read order: [`ETF_SYSTEM_INDEX.md`](./ETF_SYSTEM_INDEX.md).
- Current account, holdings, lifecycle and latest formal action: [`ETF当前状态_DASHBOARD.md`](./ETF当前状态_DASHBOARD.md).
- Real-trade CASE / OBS / historical decision review: [`ETF交易复盘与经验库_2026.md`](./ETF交易复盘与经验库_2026.md).
- Objective market, trade and account archive: [`ETF市场行情档案_2026.md`](./ETF市场行情档案_2026.md).
- Current market-data/runtime standard and provider routing: [`ETF与市场监测数据接口使用规范.md`](./ETF与市场监测数据接口使用规范.md).

Formal priority is MASTER > Dashboard > current valid market/account/actual fills > experience library > market archive > historical chat. Provider names, monitoring objects, pulse policy and fallback priority are intentionally not duplicated in this README; read the current data standard and machine configuration so the repository front page cannot drift behind production routing.

The frozen transaction workbook set is under `history/baseline_2026-08-21/` and is retained for audit/reference only. It is not an automated update input.

## Runtime principles

Market monitoring follows the current three-layer structure defined by the MASTER, Dashboard and machine configuration: formal indices; all holding ETFs plus observation ETFs; dynamic account stocks and hypothesis-driven industry-chain stocks. Every query uses actual provider timestamps, market phase, runtime health and configured freshness thresholds. Failed providers use validated fallback logic or are explicitly marked unavailable/limited; stale data is never presented as current real-time data.

Confirmed actual fills take precedence over pending decisions. A confirmed trade must be synchronized through machine trade events, account/holdings state, Dashboard, objective archive and the experience-library CASE chain. System consistency checks are intended to detect a machine-event / human-readable-record mismatch rather than allowing a partial update to remain production-complete.

## Local validation

```powershell
$env:ETF_SYSTEM_ROOT = (Get-Location).Path
python scripts/check_system_consistency.py
python scripts/cloud_runner_snapshot.py --node manual
```

The consistency check is mandatory after changes to formal files, the data standard, monitoring objects, provider policy, pulse policy, scripts or workflows. Hard inconsistencies block production; normal state gaps are surfaced explicitly rather than silently inferred.

## GitHub Actions

Configure the repository secrets required by the current provider configuration. Production workflows apply session gates, bounded retries/timeouts, controlled concurrency, freshness/quality rules and auditable runtime health. Automated workflows may maintain facts, context and audit state, but do not modify the MASTER to expand trading permission and do not place orders.
