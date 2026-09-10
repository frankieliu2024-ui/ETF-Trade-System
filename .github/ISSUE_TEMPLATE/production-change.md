---
name: Production change / incident
description: Frame ETF production incidents and maintenance at the system/failure-domain level before implementation
title: "production: "
labels: []
assignees: []
---

> Operational control surface only. Governance rules are not defined here. Start from `ETF_SYSTEM_INDEX.md`, then read the current production mutation protocol and `docs/Codex协作执行说明.md`.

## Routing and admission

- `LATEST_MAIN_AT_START=`
- `PROTOCOL_VERSION=`
- `RISK_TIER=`
- `ADMISSION=`
- `ACTION_IMPACT=`
- `EXISTING_MECHANISM_INSUFFICIENT=`
- `EVIDENCE_QUALITY=`

## System-level framing

- `SYSTEM_GOAL=`
- `FAILURE_DOMAIN=`
- `SHARED_INVARIANTS=`
- `CANONICAL_OWNER=`
- `ADJACENT_BREAKPOINTS=`
- `SAME_ROOT_OTHER_SYMPTOMS=`
- `LOCAL_PATCH_REJECTION=`
- `SYSTEM_LEVEL_SUCCESS_METRIC=`
- `CLOSURE_PROOF=`
- `REAL_PRODUCTION_EXPOSURE=`
- `NON_GOALS=`

## Execution gate

Read-only diagnosis may proceed while framing is incomplete. Implementation, production mutation, PR entry, or an executable BRIEF/PACKET must not proceed while any core system-level framing field required by the current collaboration control plane is missing or materially unresolved.

Once framing is complete, create the smallest root-cause-complete BRIEF/PACKET under the current V1.9 governance. Do not copy governance rules into this Issue, create a second checker, or create a parallel state/workflow/acceptance plane.
