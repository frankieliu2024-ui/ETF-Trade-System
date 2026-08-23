# ETF Trade System V2.2.15

This repository is the cloud runtime and canonical state repository for the ETF trading system. It collects and validates market snapshots, maintains runtime contexts, and preserves auditable state without placing orders.

The authoritative trading rules remain in the root-level `ETF规则_MASTER.md`; the canonical repository map is `ETF_SYSTEM_INDEX.md`. The root-level `ETF与市场监测数据接口使用规范.md` is the canonical data/runtime standard for the three monitoring layers, providers, pulse/freshness policy, cross-market time alignment, quality checks, and failure degradation. It is not a trading-rule source.

The frozen transaction workbook set is under `history/baseline_2026-08-21/` and is retained for audit/reference only. It is not an automated update input.

## Runtime principles

Market monitoring uses three layers: fixed formal indices; holding ETF + observation ETF; dynamic account stocks plus query-time industry-chain stocks. Production data is multi-source: Hithink is the primary source for supported A-share objects, while Yahoo Chart API is a formal source for overseas/Asian objects, with explicit proxies and validated fallbacks where required.

The target intraday pulse is 10 minutes, but cron time is not treated as market time. Every query uses actual `captured_at`, runtime health and freshness thresholds from `config/runtime_policy.json`. Failed runs preserve the last valid state; stale data is never presented as current real-time data.

## Local validation

```powershell
$env:ETF_SYSTEM_ROOT = (Get-Location).Path
python scripts/check_system_consistency.py
python scripts/cloud_runner_snapshot.py --node manual
```

The consistency check is mandatory after changes to formal files, the data standard, monitoring objects, provider policy, pulse policy, scripts or workflows. Hard inconsistencies block production; normal state gaps such as a missing current-day broker account fact are warnings.

## GitHub Actions

Configure the repository secret `HITHINK_FINANCE_API_KEY`. The market workflow applies a consistency preflight, bounded retries/timeouts, controlled concurrency, freshness/degradation rules and auditable runtime health. It does not modify the MASTER or place orders.
