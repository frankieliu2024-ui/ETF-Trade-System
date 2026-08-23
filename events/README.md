# Event ledger

`events.jsonl` stores append-only, deduplicated facts. Valid event types are `TRADE_EXECUTED`, `ACCOUNT_UPDATED`, `DATA_ERROR`, `SYSTEM_ERROR`, `RULE_VERSION_CHANGE`, and `MANUAL_OVERRIDE`.

The Phase 2 state layer never invents account or trade facts. A repeated deterministic event ID is ignored, and every accepted event records its source, creation time, payload, and Git commit.
