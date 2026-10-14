# Scheduled Review Protected Baseline

- Baseline source: `main`
- Baseline commit: `ac3d618d6dba391d8126513c4bad39c80e7b2da6`
- Created: 2026-10-14 Asia/Shanghai
- Purpose: protect the already verified notification behavior of `ETF系统复核` and `ETF交易复盘` while evaluating a separate Formal Decision repair.
- This is a protection baseline, not a permanent freeze and not a replacement for current main.

## Protected behavior

1. The task produces one complete frozen `FINAL_CONTENT`.
2. The notification is an original-content projection, not a newly generated summary.
3. The canonical outer titles are exactly `【ETF系统复核】` and `【ETF交易复盘】`.
4. The task/run/report identity, full content, and content hash remain bound in the completion event.
5. The existing notification workflow and notification center remain the single external-effect path.
6. Current-event identity is selected from the triggering event; ambiguous candidates fail closed.
7. Notification state persistence is separate from sending and must not resend a successfully delivered message.
8. A workflow success label alone is not treated as user delivery success.

## Protected implementation surface

- `.github/workflows/decision-notification.yml`
- `scripts/notification_center.py`
- `scripts/run_guarded_notification.py`
- `scripts/merge_notification_state.py`
- `scripts/scheduled_review_completion.py`
- `docs/ETF主动通知体系.md`
- `tests/test_scheduled_review_completion.py`
- `tests/test_notification_workflow_structure.py`
- Scheduled task prompt contracts and fixed-title contracts.

## Change boundary

Formal Decision work must not alter the scheduled-review schedules, task semantics, fixed titles, completion binding, report content contract, or single-owner notification architecture. If shared notification code is changed, the two scheduled-review contracts must be re-run and pass before any merge proposal.

This file records the baseline and expected invariants only. It does not authorize a merge, production task execution, real PushPlus delivery, or any trading action.
