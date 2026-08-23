# ETF Trade System V2.2.15

This repository is the Phase 1 cloud-infrastructure migration branch. It is data-only: it collects and validates market snapshots, preserves raw responses, and maintains `data/state/CURRENT.json`.

The authoritative trading rules remain in `docs/rules/ETF规则_MASTER.md`; the canonical repository map is `ETF_SYSTEM_INDEX.md`. This repository does not place API keys in files, does not generate trade recommendations, does not modify the MASTER, and does not place orders.

The frozen transaction workbook set is under `history/baseline_2026-08-21/` and is retained for audit/reference only. It is not an automated update input.

## Local validation

```powershell
$env:ETF_SYSTEM_ROOT = (Get-Location).Path
python scripts/cloud_runner_snapshot.py --node manual
```

On weekends and exchange holidays the runner exits without producing a valid node. On a trading day it requires `HITHINK_FINANCE_API_KEY` from the environment and writes a timestamped JSON snapshot plus `data/state/CURRENT.json`.

## GitHub Actions

Configure the repository secret `HITHINK_FINANCE_API_KEY`. The workflow installs the official CLI package, applies bounded retries/timeouts, and commits only market data artifacts. It never receives account facts or changes the rule files.
