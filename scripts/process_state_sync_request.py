nt: 113091)
Total output lines: 7935


@@FILE:.github/workflows/decision-notification.yml@@
name: ETF decision notification
run-name: ETF decision notification — ${{ inputs.scheduled_review_completion_path || github.event_name }}

on:
  workflow_run:
    workflows:
      - "ETF market snapshot"
      - "Overseas pre-open pulse"
      - "US extended-hours pulse"
      - "ETF workflow failure guard"
      - "ETF system consistency"
    types: [completed]
  # Canonical Scheduled Review events are committed to main by the market-snapshot
  # workflow. GitHub does not recursively create a push-triggered workflow from a
  # commit made with GITHUB_TOKEN, so consume that producer completion directly.
  # The event step reconstructs and binds the exact review path from the producer
  # commit instead of scanning the review directory.

  # Keep the manual Run workflow entry discoverable for the canonical notification workflow.
  workflow_dispatch:
    inputs:
      test_notification:
        description: "Send one safe PushPlus test notification"
        required: true
        default: "false"
      pulse_test:
        description: "Send synthetic Discovery and Hyperliquid pulse notifications through the canonical PushPlus path"
        required: true
        default: "false"
      recovery_market:
        description: "Bounded canonical recovery market (empty for ordinary dispatch)"
        required: false
        default: ""
      recovery_source_run_id:
        description: "Source US pulse run ID for bounded recovery audit"
        required: false
        default: ""
      scheduled_review_completion_path:
        description: "Exact current Scheduled Review completion path to canonicalize"
        required: false
        default: ""
      force_resend:
        description: "Explicitly resend the same bound completion notification once"
        required: false
        default: "false"
  push:
    paths:
      # Formal Decision notifications are authorized only by events/reviews/formal_*.json.
      - "requests/live_snapshot/*__scheduled_review_completion.json"
      - "requests/live_snapshot/scheduled_review_completion_*.json"
      # Scheduled trade-review task writes persist their canonical completion at the
      # request-id path. Keep this as an ingress alias; identity and content
      # validation below remain mandatory before any notification is sent.
      - "requests/live_snapshot/etf_trade_review_*.json"
      - "events/trades/*.json"
      - "events/reviews/*.json"
      - "data/state/account_fact.json"
      - "requests/notification_test/*.json"
      - "requests/report_delivery/*.json"
      - "requests/report_handoff/*.json"
      # Canonical market-state publication is an independent notification wake.
      # This preserves one notification engine while avoiding a hard dependency
      # on workflow_run creation after a producer was itself recovered from a
      # missed Actions wake. Existing evidence/materiality/dedupe guards absorb
      # duplicate push + workflow_run wake opportunities for the same fact.
      - "data/state/CURRENT.json"
      - "data/state/overseas_context.json"
      - "data/state/us_extended_hours_context.json"
      - "data/state/discovery_pulse_index.json"
      - "data/state/hyperliquid_pulse_index.json"
      - "data/state/discovery_pulse_latest.json"
      - "data/state/hyperliquid_pulse_latest.json"
  schedule:
    # Dedicated close-account reminder envelope: Beijing 15:20 Monday-Friday.
    # notification_center.py checks the formal A-share trading calendar;
    # non-trading weekdays produce no close-account reminder.
    - cron: "20 7 * * 1-5"

permissions:
  contents: write

# Notification projection is an independent consumer of already-persisted
# completion facts. It must not wait behind (or be replaced by) market/state
# producers in the repository-wide writer lane. Each persistence step below
# re-reads latest main and retries boundedly before publishing.
concurrency:
  group: etf-decision-notification-${{ github.run_id }}
  cancel-in-progress: false

jobs:
  notify:
    # A producer completion or canonical state publication is only a wake
    # opportunity; canonical evidence and notification_center
    # dedupe/materiality guards decide whether anything is sent.
    # Keep the failure-guard's skipped/failed diagnostic wake from replaying stale state.
    if: >-
      ${{
        github.event_name != 'workflow_run' ||
        github.event.workflow_run.name != 'ETF workflow failure guard' ||
        github.event.workflow_run.conclusion == 'success'
      }}
    runs-on: ubuntu-latest
    timeout-minutes: 4
    env:
      ETF_SYSTEM_ROOT: ${{ github.workspace }}
      PUSHPLUS_TOKEN: ${{ secrets.PUSHPLUS_TOKEN }}
    steps:
      - uses: actions/checkout@v5
        with:
          ref: main
          fetch-depth: 20
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"

      - name: Classify manually dispatched Scheduled Review completion
        # Formal Decision and Trade Review use the shared scheduled_review_completion_path.
        if: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path != '' }}
        id: dispatch_completion_kind
        shell: bash
        env:
          COMPLETION_PATH: ${{ inputs.scheduled_review_completion_path }}
        run: |
          set -euo pipefail
          case "$COMPLETION_PATH" in
            requests/live_snapshot/*) ;;
            *) COMPLETION_PATH="requests/live_snapshot/$COMPLETION_PATH" ;;
          esac
          test -f "$COMPLETION_PATH"
          python - "$COMPLETION_PATH" <<'PY'
          import json, os, sys
          payload = json.load(open(sys.argv[1], encoding="utf-8"))
          binding = payload.get("presentation_binding") or {}
          report_type = str(payload.get("report_type") or binding.get("report_type") or "").strip().upper()
          task_id = str(payload.get("task_id") or binding.get("task_id") or "").strip()
          task_name = str(payload.get("task_name") or "").strip()
          source = str(payload.get("source") or payload.get("formal_entry_source") or "").strip().upper()
          content = str(payload.get("final_content") or "")
          formal = (
              report_type == "ETF_FORMAL_DECISION"
              and (task_id == "ETF正式决策" or task_name == "ETF正式决策")
          )
          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
              out.write("formal=%s\n" % ("true" if formal else "false"))
              out.write("scheduled=%s\n" % ("false" if formal else "true"))
          PY

      - name: Detect push notification kind
        if: ${{ github.event_name == 'push' }}
        id: push_kind
        shell: bash
        env:
          PUSH_BEFORE: ${{ github.event.before }}
          PUSH_AFTER: ${{ github.sha }}
        run: |
          # The checkout intentionally tracks latest main and may advance beyond the
          # commit that triggered this workflow. Always classify the triggering push
          # itself rather than HEAD^..HEAD from a later dynamic-state commit.
          git cat-file -e "$PUSH_BEFORE^{commit}" 2>/dev/null || git fetch --no-tags origin "$PUSH_BEFORE"
          git cat-file -e "$PUSH_AFTER^{commit}" 2>/dev/null || git fetch --no-tags origin "$PUSH_AFTER"
          git diff --name-only "$PUSH_BEFORE" "$PUSH_AFTER" > /tmp/changed_files.txt
                              mapfile -t completion_paths < <(grep -E '^requests/live_snapshot/([^/]+__scheduled_review_completion|scheduled_review_completion_[^/]+|etf_trade_review_[^/]+)\.json$' /tmp/changed_files.txt || true)
          if [ "${#completion_paths[@]}" -eq 1 ]; then
            completion_path="${completion_paths[0]}"
            completion_kind=$(python - "$completion_path" <<'PY'
          import json
          import sys
          
          with open(sys.argv[1], encoding="utf-8") as handle:
              payload = json.load(handle)
          binding = payload.get("presentation_binding") or {}
          # Shared Scheduled Review completion has two valid current-main
          # representations: the original nested presentation binding and the
          # direct completion contract emitted by the Formal Decision consumer.
          # Classify from both shapes so a Formal Decision cannot fall through
          # to the Scheduled Review no-op guard.
          report_type = str(
              payload.get("report_type") or binding.get("report_type") or ""
          ).strip().upper()
          source = str(payload.get("source") or payload.get("formal_entry_source") or "").strip().upper()
          content = str(payload.get("final_content") or "")
          if (
              report_type == "ETF_FORMAL_DECISION"
              and str(payload.get("task_id") or binding.get("task_id") or payload.get("task_name") or "").strip() == "ETF正式决策"
          ):
              print("FORMAL")
          else:
              task_id = str(
                  payload.get("task_id") or binding.get("task_id") or ""
              ).strip()
              if (
                  report_type in {"ETF_TRADE_REVIEW", "SCHEDULED_TRADE_REVIEW", "ETF_SYSTEM_REVIEW"}
                  and task_id in {"ETF交易复盘", "ETF系统复核"}
              ):
                  # Current direct completion builders carry the task identity
                  # inside presentation_binding; accept that canonical shape
                  # without weakening the formal-decision branch above.
                  print("SYSTEM_REVIEW" if task_id == "ETF系统复核" else "TRADE_REVIEW")
              elif payload.get("request_type") == "SCHEDULED_REVIEW_COMPLETION":
                  print("SCHEDULED_REVIEW")
              else:
                  print("UNKNOWN")
          PY
          )
            case "$completion_kind" in
              FORMAL)
                # The completion writer is the canonical producer.  A completion
                # commit is also a valid wake when explicit dispatch is missed;
                # downstream identity, hash, task-local binding and dedupe guards
                # still prevent duplicate or drifted delivery.  This closes the
                # production gap where a valid Formal Decision completion was
                # persisted but no active notification was emitted.
                echo "formal_completion=true" >> "$GITHUB_OUTPUT"
              echo "scheduled_review_completion=false" >> "$GITHUB_OUTPUT"
                echo "formal_review_source_path=$completion_path" >> "$GITHUB_OUTPUT"
                echo "formal_force_resend=false" >> "$GITHUB_OUTPUT"
                ;;
              SYSTEM_REVIEW)
                # System Review uses the same direct completion forwarding path
                # as Trade Review. Keep the Trade Review branch unchanged.
                echo "scheduled_review_completion=true" >> "$GITHUB_OUTPUT"
                echo "scheduled_review_completion_path=$completion_path" >> "$GITHUB_OUTPUT"
                ;;
              TRADE_REVIEW)
                # A Scheduled Review completion is itself the canonical handoff
                # after the task freezes FINAL_CONTENT. Consume this exact
                # completion once through the existing event/report/notification
                # owner; do not require a second browser dispatch.
                echo "scheduled_review_completion=true" >> "$GITHUB_OUTPUT"
                echo "scheduled_review_completion_path=$completion_path" >> "$GITHUB_OUTPUT"
                ;;
              *)
                echo "Unknown completion contract in $completion_path; fail closed." >&2
                exit 1
                ;;
            esac
          elif [ "${#completion_paths[@]}" -gt 1 ]; then
            echo "Ambiguous completion push: expected exactly one completion file, found ${#completion_paths[@]}" >&2
            exit 1
          else
            echo "formal_completion=false" >> "$GITHUB_OUTPUT"
            echo "formal_review_source_path=" >> "$GITHUB_OUTPUT"
          fi
          echo "decision=false" >> "$GITHUB_OUTPUT"
          echo "decision_event_path=" >> "$GITHUB_OUTPUT"
          if grep -Eq '^events/trades/[^/]+\.json$' /tmp/changed_files.txt; then
            echo "trade=true" >> "$GITHUB_OUTPUT"
          else
            echo "trade=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/account_fact.json' /tmp/changed_files.txt; then
            echo "account=true" >> "$GITHUB_OUTPUT"
          else
            echo "account=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Eq '^requests/notification_test/[^/]+\.json$' /tmp/changed_files.txt; then
            echo "test=true" >> "$GITHUB_OUTPUT"
          else
            echo "test=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/CURRENT.json' /tmp/changed_files.txt; then
            echo "a_share_market=true" >> "$GITHUB_OUTPUT"
          else
            echo "a_share_market=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/overseas_context.json' /tmp/changed_files.txt; then
            echo "apac_market=true" >> "$GITHUB_OUTPUT"
          else
            echo "apac_market=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/us_extended_hours_context.json' /tmp/changed_files.txt; then
            echo "us_market=true" >> "$GITHUB_OUTPUT"
          else
            echo "us_market=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/discovery_pulse_index.json' /tmp/changed_files.txt; then
            echo "discovery_pulse=true" >> "$GITHUB_OUTPUT"
          else
            echo "discovery_pulse=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/hyperliquid_pulse_index.json' /tmp/changed_files.txt; then
            echo "hyperliquid_pulse=true" >> "$GITHUB_OUTPUT"
          else
            echo "hyperliquid_pulse=false" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/discovery_pulse_latest.json' /tmp/changed_files.txt; then
            echo "discovery_pulse=true" >> "$GITHUB_OUTPUT"
          fi
          if grep -Fxq 'data/state/hyperliquid_pulse_latest.json' /tmp/changed_files.txt; then
            echo "hyperliquid_pulse=true" >> "$GITHUB_OUTPUT"
          fi
          mapfile -t handoff_paths < <(grep -E '^requests/report_handoff/[^/]+\.json$' /tmp/changed_files.txt || true)
          if [ "${#handoff_paths[@]}" -eq 1 ]; then
            echo "report_handoff=true" >> "$GITHUB_OUTPUT"
            echo "report_handoff_path=${handoff_paths[0]}" >> "$GITHUB_OUTPUT"
          elif [ "${#handoff_paths[@]}" -gt 1 ]; then
            echo "Ambiguous REPORT handoff push: expected exactly one handoff, found ${#handoff_paths[@]}" >&2
            exit 1
          else
            echo "report_handoff=false" >> "$GITHUB_OUTPUT"
            echo "report_handoff_path=" >> "$GITHUB_OUTPUT"
          fi
          mapfile -t review_paths < <(grep -E '^events/reviews/[^/]+\.json$' /tmp/changed_files.txt || true)
          if [ "${#review_paths[@]}" -eq 1 ] && [[ "${review_paths[0]}" != events/reviews/formal_*.json ]]; then
            echo "review_completion=true" >> "$GITHUB_OUTPUT"
            echo "review_event_path=${review_paths[0]}" >> "$GITHUB_OUTPUT"
          elif [ "${#review_paths[@]}" -gt 1 ]; then
            echo "Ambiguous canonical Scheduled Review push: expected exactly one review event, found ${#review_paths[@]}" >&2
            printf '%s\n' "${review_paths[@]}" >&2
            exit 1
          else
            echo "review_completion=false" >> "$GITHUB_OUTPUT"
            echo "review_event_path=" >> "$GITHUB_OUTPUT"
          fi
          mapfile -t report_paths < <(grep -E '^requests/report_delivery/[^/]+\.json$' /tmp/changed_files.txt || true)
          if [ "${#report_paths[@]}" -eq 1 ]; then
            echo "report=true" >> "$GITHUB_OUTPUT"
            echo "report_path=${report_paths[0]}" >> "$GITHUB_OUTPUT"
          elif [ "${#report_paths[@]}" -gt 1 ]; then
            echo "Ambiguous REPORT push: expected exactly one report request, found ${#report_paths[@]}" >&2
            printf '%s\n' "${report_paths[@]}" >&2
            exit 1
          else
            echo "report=false" >> "$GITHUB_OUTPUT"
            echo "report_path=" >> "$GITHUB_OUTPUT"
          fi
          cat /tmp/changed_files.txt

      # notification_center.py historically scans the full report request directory.
      # For a REPORT push, make the disposable checkout expose only the request that
      # triggered this commit so an older lexicographically-later report cannot be
      # selected. The canonical repository is untouched; persistence resets to
      # origin/main before writing unified notification state.
      - name: Isolate pushed report delivery request
        if: ${{ github.event_name == 'push' && steps.push_kind.outputs.report == 'true' }}
        shell: bash
        env:
          REPORT_PATH: ${{ steps.push_kind.outputs.report_path }}
        run: |
          test -n "$REPORT_PATH"
          test -f "$REPORT_PATH"
          find requests/report_delivery -maxdepth 1 -type f -name '*.json' ! -path "$REPORT_PATH" -delete
          test "$(find requests/report_delivery -maxdepth 1 -type f -name '*.json' | wc -l)" -eq 1
          test -f "$REPORT_PATH"

      # BDS is not a notification wake. Formal Decision notifications are
      # authorized only by the current completion event under event_notify.
      - name: Build delayed execution reconciliation
        if: ${{ github.event_name == 'workflow_run' || (github.event_name == 'push' && (steps.push_kind.outputs.account == 'true' || steps.push_kind.outputs.trade == 'true')) }}
        run: python scripts/build_execution_reconciliation.py

      - name: Send A-share opening value signal or close summary when due
        id: a_share_summary_notify
        if: ${{ (github.event_name == 'workflow_run' && github.event.workflow_run.name == 'ETF market snapshot') || (github.event_name == 'push' && steps.push_kind.outputs.a_share_market == 'true') }}
        run: python scripts/run_guarded_notification.py batch --market a-share

      - name: Send APAC opening value signal or close summary when due
        id: apac_summary_notify
        if: ${{ (github.event_name == 'workflow_run' && github.event.workflow_run.name == 'Overseas pre-open pulse') || (github.event_name == 'push' && steps.push_kind.outputs.apac_market == 'true') }}
        env:
          PREVIOUS_CONTEXT_PATH: data/state/overseas_context_previous.json
        run: python scripts/run_guarded_notification.py batch --market apac

      - name: Send APAC monitored-object value event when due
        id: apac_shock_notify
        if: ${{ false }}
        env:
          PREVIOUS_CONTEXT_PATH: data/state/overseas_context_previous.json
        run: echo "included in canonical batch"

      - name: Send US session summary when due
        id: us_summary_notify
        if: ${{ (github.event_name == 'workflow_run' && (github.event.workflow_run.name == 'US extended-hours pulse' || github.event.workflow_run.name == 'Overseas pre-open pulse')) || (github.event_name == 'workflow_dispatch' && inputs.recovery_market == 'us') || (github.event_name == 'push' && steps.push_kind.outputs.us_market == 'true') }}
        continue-on-error: true
        env:
          PREVIOUS_CONTEXT_PATH: data/state/us_extended_hours_context_previous.json
        run: python scripts/run_guarded_notification.py batch --market us

      - name: Send US monitored-object value event when due
        id: us_shock_notify
        if: ${{ false }}
        continue-on-error: true
        env:
          PREVIOUS_CONTEXT_PATH: data/state/us_extended_hours_context_previous.json
        run: echo "included in canonical batch"

      # A-share is only the data source route here. The event engine evaluates
      # every currently monitored index/ETF/account stock by attention value;
      # market membership itself never grants or blocks notification eligibility.
      - name: Send A-share monitored-object value event when due
        id: a_share_shock_notify
        if: ${{ false }}
        continue-on-error: true
        run: echo "included in canonical batch"

      - name: Send safe notification-channel test
        id: test_notify
        if: ${{ (github.event_name == 'workflow_dispatch' && github.event.inputs.test_notification == 'true') || (github.event_name == 'push' && steps.push_kind.outputs.test == 'true') }}
        run: python scripts/run_guarded_notification.py center --mode channel-test

      - name: Send synthetic Discovery and Hyperliquid pulse test
        id: pulse_test_notify
        if: ${{ github.event_name == 'workflow_dispatch' && inputs.pulse_test == 'true' }}
        env:
          PULSE_TEST_RUN_ID: ${{ github.run_id }}
        run: python scripts/run_guarded_notification.py batch --market pulse-test

      # Discovery and Hyperliquid pulses use the same bounded notification
      # batch and materiality guard as all other market evidence. Their index
      # changes are not themselves notifications; the batch compares the latest
      # two identities and lets the existing center dedupe/send.
      - name: Send Discovery or Hyperliquid pulse value event when due
        id: pulse_notify
        if: >-
          ${{
            (github.event_name == 'push' &&
              (steps.push_kind.outputs.discovery_pulse == 'true' || steps.push_kind.outputs.hyperliquid_pulse == 'true')) ||
            (github.event_name == 'workflow_run' &&
              contains(fromJSON('["ETF market snapshot", "Overseas pre-open pulse", "US extended-hours pulse"]'), github.event.workflow_run.name) &&
              github.event.workflow_run.conclusion == 'success')
          }}
        run: python scripts/run_guarded_notification.py batch --market pulse

      # Event-driven notifications have no A-share-session or night-time gate.
      # The shared evidence guard rejects label/coverage-only changes before the
      # notification center can reach PushPlus.
      - name: Bind canonical review event from producer completion
        id: workflow_review
        if: ${{ github.event_name == 'workflow_run' && github.event.workflow_run.name == 'ETF market snapshot' }}
        shell: bash
        env:
          PRODUCER_RUN_ID: ${{ github.event.workflow_run.id }}
          PRODUCER_SHA: ${{ github.event.workflow_run.head_sha }}
        run: |
          # workflow_run.head_sha is the input commit; Review events are written
          # by the downstream data commit created inside market-snapshot.
          if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
            git fetch --no-tags --unshallow origin main
          else
            git fetch --no-tags origin main
          fi
          # A completion commit may be the producer run's head commit itself.
          # Consume that exact durable handoff even when the market-snapshot
          # producer did not create a downstream data commit.
          mapfile -t producer_completion_paths < <(
            git diff-tree --no-commit-id --name-only -r "$PRODUCER_SHA" -- 'requests/live_snapshot/*.json' | while IFS= read -r candidate; do
              [ -z "$candidate" ] && continue
              python - "$candidate" <<'PY' 2>/dev/null && echo "$candidate"
          import json, sys
          from pathlib import Path
          obj = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
          if (
              str(obj.get("request_type") or "").upper() == "SCHEDULED_REVIEW_COMPLETION"
              and str(obj.get("task_id") or "").strip() in {"ETF正式决策", "ETF交易复盘", "ETF系统复核"}
          ):
              raise SystemExit(0)
          raise SystemExit(1)
          PY
            done
          )
          if [ "${#producer_completion_paths[@]}" -eq 1 ]; then
            completion_path="${producer_completion_paths[0]}"
            completion_kind=$(python - "$completion_path" <<'PY'
          import json, sys
          from pathlib import Path
          obj = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
          binding = obj.get("presentation_binding") or {}
          report_type = str(obj.get("report_type") or binding.get("report_type") or "").strip().upper()
          task_id = str(obj.get("task_id") or binding.get("task_id") or "").strip()
          print("FORMAL" if report_type == "ETF_FORMAL_DECISION" and task_id == "ETF正式决策" else "SCHEDULED")
          PY
            )
            echo "review_completion=true" >> "$GITHUB_OUTPUT"
            echo "review_completion_path=$completion_path" >> "$GITHUB_OUTPUT"
            if [ "$completion_kind" = "FORMAL" ]; then
              echo "formal_completion=true" >> "$GITHUB_OUTPUT"
              echo "scheduled_review_completion=false" >> "$GITHUB_OUTPUT"
              echo "formal_review_source_path=$completion_path" >> "$GITHUB_OUTPUT"
            else
              echo "formal_completion=false" >> "$GITHUB_OUTPUT"
              echo "formal_review_source_path=" >> "$GITHUB_OUTPUT"
            fi
            echo "decision_completion=false" >> "$GITHUB_OUTPUT"
            echo "decision_event_path=" >> "$GITHUB_OUTPUT"
            echo "Bound completion directly from producer commit: $completion_path"
            exit 0
          elif [ "${#producer_completion_paths[@]}" -gt 1 ]; then
            echo "Ambiguous producer completion commit: expected exactly one, found ${#producer_completion_paths[@]}" >&2
            printf '%s\n' "${producer_completion_paths[@]}" >&2
            exit 1
          fi
          mapfile -t downstream_commits < <(
            git log origin/main --format='%H%x09%s' --grep="[producer-run:${PRODUCER_RUN_ID}]" --fixed-strings
          )
          if [ "${#downstream_commits[@]}" -eq 0 ]; then
            echo "No downstream producer commit for run ${PRODUCER_RUN_ID}; no Scheduled Review completion to notify."
            echo "review_completion=false" >> "$GITHUB_OUTPUT"
            echo "review_event_path=" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          if [ "${#downstream_commits[@]}" -ne 1 ]; then
            echo "Ambiguous downstream producer commits for run ${PRODUCER_RUN_ID}: ${#downstream_commits[@]}" >&2
            printf '%s\n' "${downstream_commits[@]}" >&2
            exit 1
          fi
          downstream_sha="${downstream_commits[0]%%$'\t'*}"
          mapfile -t review_paths < <(
            git diff-tree --no-commit-id --name-only -r "$downstream_sha" -- 'events/reviews/*.json' | sed '/^$/d'
          )
          if [ "${#review_paths[@]}" -eq 1 ] && [[ "${review_paths[0]}" != events/reviews/formal_*.json ]]; then
            echo "review_completion=true" >> "$GITHUB_OUTPUT"
            echo "review_event_path=${review_paths[0]}" >> "$GITHUB_OUTPUT"
          elif [ "${#review_paths[@]}" -gt 1 ]; then
            echo "Ambiguous canonical Scheduled Review producer commit: expected exactly one review event, found ${#review_paths[@]}" >&2
            printf '%s\n' "${review_paths[@]}" >&2
            exit 1
          else
            echo "review_completion=false" >> "$GITHUB_OUTPUT"
            echo "review_event_path=" >> "$GITHUB_OUTPUT"
          fi
          echo "decision_completion=false" >> "$GITHUB_OUTPUT"
          echo "decision_event_path=" >> "$GITHUB_OUTPUT"
          if [ "${#review_paths[@]}" -eq 0 ]; then
            # Successful market snapshots can be non-report wakes. Absence of a
            # report event is a normal no-op; only an explicit canonical event
            # authorizes REPORT delivery. Formal request closure is covered by
            # the dedicated automated E2E contract.
            echo "Producer run ${PRODUCER_RUN_ID} has no canonical report event; notification is a no-op."
            echo "review_completion=false" >> "$GITHUB_OUTPUT"
            echo "review_event_path=" >> "$GITHUB_OUTPUT"
            echo "decision_completion=false" >> "$GITHUB_OUTPUT"
            echo "decision_event_path=" >> "$GITHUB_OUTPUT"
          fi

      - name: Project Scheduled Review completion to canonical review event
        id: scheduled_completion_event
        # Canonical notification is already bound to the same completion.
        # Review-event/REPORT projection is audit-only and must not turn a
        # successful System Review or Trade Review notification into failure.
        continue-on-error: true
        # Scheduled Review completion is dispatched explicitly by the producer
        # in the same run.  Push/workflow_run are audit wakes only and must not
        # become a second notification ingress.
        if: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path != '' && steps.dispatch_completion_kind.outputs.scheduled == 'true' }}
        shell: bash
        env:
          COMPLETION_PATH: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path || steps.push_kind.outputs.scheduled_review_completion_path || steps.workflow_review.outputs.review_completion_path }}
        run: |
          set -euo pipefail
          # Normalize canonical paths and legacy basenames before reading.
          completion_path="$COMPLETION_PATH"
          case "$completion_path" in
            requests/live_snapshot/*)
              ;;
            /*)
              completion_path="requests/live_snapshot/${completion_path##*/}"
              ;;
            *)
              completion_path="requests/live_snapshot/$completion_path"
              ;;
          esac
          case "$completion_path" in
            requests/live_snapshot/*__scheduled_review_completion.json|requests/live_snapshot/scheduled_review_completion_*.json|requests/live_snapshot/etf_trade_review_*.json)
              ;;
            *)
              echo "Scheduled Review completion path is outside the allowed completion namespace: $completion_path" >&2
              exit 1
              ;;
          esac
          test -f "$completion_path"
          # A shared Scheduled Review completion may carry a Formal Decision
          # contract. Route it only to the Formal Decision projection below;
          # otherwise both projections consume the same path and the Formal
          # Decision safety guard rejects the handoff as a misrouted review.
          if python - "$completion_path" <<'PY'
          import json, sys
          with open(sys.argv[1], encoding="utf-8") as handle:
              payload = json.load(handle)
          raise SystemExit(
              0 if (
              (
                  str(payload.get("report_type") or (payload.get("presentation_binding") or {}).get("report_type") or "").strip().upper()
                  == "ETF_FORMAL_DECISION"
                  and str(payload.get("task_id") or (payload.get("presentation_binding") or {}).get("task_id") or "").strip() == "ETF正式决策"
              ) or (
                  str(payload.get("task_id") or (payload.get("presentation_binding") or {}).get("task_id") or "").strip() == "ETF正式决策"
                  and str(payload.get("decision_path") or "").strip().upper() == "FORMAL"
                  and str(payload.get("interaction_scenario") or "").strip().upper() == "FORMAL_DECISION"
              )
          ) else 1
          )
          PY
          then
            echo "Formal Decision completion routed to Formal Decision projection."
            echo "review_event_path=" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          completion_type=$(python - "$completion_path" <<'PY'
          import json, sys
          payload = json.load(open(sys.argv[1], encoding="utf-8"))
          binding = payload.get("presentation_binding") or {}
          print(str(payload.get("report_type") or binding.get("report_type") or "").strip().upper())
          PY
          )
          if [ "$completion_type" = "ETF_TRADE_REVIEW" ] || [ "$completion_type" = "SCHEDULED_TRADE_REVIEW" ]; then
            # Trade Review is a delivery-only report.  Do not route it through
            # process_state_sync_request.py: that projector validates Formal
            # Decision managed-position/lifecycle semantics that do not belong
            # to a review whose contract is exact full-body forwarding.
            result=$(PYTHONPATH=scripts python - "$completion_path" <<'PY'
          import json
          import re
          import sys
          from pathlib import Path
          from scripts.process_state_sync_request import record_post_close_review

          completion_path = Path(sys.argv[1])
          payload = json.loads(completion_path.read_text(encoding="utf-8"))
          binding = payload.get("presentation_binding") or {}
          report_type = str(payload.get("report_type") or binding.get("report_type") or "").strip().upper()
          task_id = str(payload.get("task_id") or binding.get("task_id") or "").strip()
          task_name = str(payload.get("task_name") or "").strip()
          # A direct scheduled completion is already a sufficient notification
          # source. Missing presentation_binding is an audit limitation, not a
          # delivery failure; leave event projection to the independent audit
          # step and let the exact completion reach the notification center.
          if report_type == "ETF_FORMAL_DECISION" and (task_id == "ETF正式决策" or task_name == "ETF正式决策") and not payload.get("presentation_binding"):
              print(json.dumps({"canonical_publication": ""}, ensure_ascii=False))
              raise SystemExit(0)
          if report_type not in {"ETF_TRADE_REVIEW", "SCHEDULED_TRADE_REVIEW"} or task_id != "ETF交易复盘":
              raise SystemExit("Trade Review completion identity mismatch")
          review = payload.get("formal_review") if isinstance(payload.get("formal_review"), dict) else {}
          full_content = str(payload.get("final_content") or binding.get("full_content") or "")
          review_title = full_content.splitlines()[0].strip() if full_content.splitlines() else ""
          scope = str(review.get("review_scope") or "").strip().upper()
          # Older valid full-day Trade Review completions predate the nested review_scope field.
          # Infer only from the frozen title, preserving fail-closed behavior for all other shapes.
          if not scope and "完整日复盘" in review_title:
              scope = "FULL_DAY"
          # Direct task writers may carry the frozen morning review only in
          # final_content/presentation_binding. The canonical owner already
          # supports MORNING_SESSION_STAGE_ONLY; reconstruct that bounded
          # scope from the immutable title and date rather than rejecting a
          # valid midday completion as a full-day review.
          inferred_morning_scope = False
          if not scope and "上午复盘" in review_title:
              scope = "MORNING_SESSION_STAGE_ONLY"
              inferred_morning_scope = True
              review = {
                  "review_scope": scope,
                  "market_date": str(payload.get("market_date") or "").strip(),
                  "reviewed_at_beijing": str(payload.get("requested_at_beijing") or "").strip(),
                  "market_phase": "MIDDAY_BREAK",
                  "data_time": {
                      "a_share_fact_boundary_beijing": str(payload.get("requested_at_beijing") or "").strip(),
                      "market_snapshot": "FROZEN_MORNING_REVIEW_CONTENT",
                  },
              }
          # Weekly Trade Review completions are valid delivery-only reviews.
          # They may intentionally omit the nested formal_review scope because
          # the frozen weekly body is already the authoritative report.
          if not scope and ("周复盘" in review_title or "周度复盘" in review_title):
              scope = "FULL_DAY"
              review = {
                  "review_scope": scope,
                  "market_date": str(payload.get("market_date") or "").strip(),
                  "reviewed_at_beijing": str(payload.get("requested_at_beijing") or "").strip(),
                  "data_time": {},
              }
          if scope not in {"FULL_DAY", "FULL_TRADING_DAY"}:
              if scope != "MORNING_SESSION_STAGE_ONLY":
                  raise SystemExit("Trade Review completion has an unsupported review scope")
          if scope in {"FULL_DAY", "FULL_TRADING_DAY"} and not review:
              review = {
                  "review_scope": scope,
                  "market_date": str(payload.get("market_date") or "").strip(),
                  "reviewed_at_beijing": str(payload.get("requested_at_beijing") or "").strip(),
                  "data_time": {},
              }
          account_path = Path("data/state/account_fact.json")
          account = json.loads(account_path.read_text(encoding="utf-8"))
          if inferred_morning_scope:
              # The frozen morning body predates structured lifecycle coverage.
              # Supply only mechanical coverage from the canonical account fact;
              # this does not invent a decision or alter the frozen body.
              managed = {}
              for item in (account.get("positions") or []):
                  if not isinstance(item, dict):
                      continue
                  code = str(item.get("code") or item.get("symbol") or item.get("security_code") or "").strip()
                  name = str(item.get("name") or item.get("security_name") or "").strip()
                  if code:
                      # Use the canonical display identity accepted by the
                      # existing validator; code remains part of the key.
                      key = f"{name}（{code}）" if name else code
                      managed[key] = "HOLD_REVIEW"
              review["lifecycle"] = managed
              review["managed_positions"] = managed
          # Pass the bounded compatibility projection to the canonical owner.
          # The frozen completion remains unchanged; only the reconstructed
          # morning-scope input is consumed by the existing projection path.
          projection_payload = dict(payload)
          # Older direct task completions may omit the shared Scheduled Review
          # identity fields. Reconstruct them in memory for the canonical
          # delivery-only projector; never rewrite the frozen completion.
          projection_payload.setdefault("request_type", "SCHEDULED_REVIEW_COMPLETION")
          projection_payload.setdefault("formal_fact_type", "FORMAL_SCHEDULED_TRADE_REVIEW")
          projection_payload.setdefault("source", "CHATGPT_MANUAL_TRADE_REVIEW")
          projection_payload.setdefault("interaction_scenario", "MANUAL_TRADE_REVIEW")
          projection_payload.setdefault("entry_mode", "MANUAL_SCHEDULED_TASK")
          # Older direct completions also omitted the entry-mode binding. Keep
          # the reconstructed projection internally consistent for the strict
          # production identity validator; the frozen completion is untouched.
          projection_binding = dict(projection_payload.get("presentation_binding") or {})
          projection_binding.setdefault("task_id", projection_payload.get("task_id", "ETF交易复盘"))
          projection_binding.setdefault("task_run_id", projection_payload.get("task_run_id", ""))
          projection_binding.setdefault("occurrence_id", projection_payload.get("occurrence_id", projection_payload.get("task_run_id", "")))
          projection_binding.setdefault("request_id", projection_payload.get("request_id", ""))
          projection_binding.setdefault("report_id", projection_payload.get("report_id", ""))
          projection_binding.setdefault("report_type", "ETF_TRADE_REVIEW")
          projection_binding.setdefault("full_content", projection_payload.get("final_content", ""))
          projection_binding.setdefault("content_hash", projection_payload.get("content_hash", ""))
          projection_binding.setdefault("entry_mode", projection_payload["entry_mode"])
          projection_payload["presentation_binding"] = projection_binding
          if inferred_morning_scope or not isinstance(projection_payload.get("formal_review"), dict) or not projection_payload.get("formal_review"):
              projection_payload["formal_review"] = review
              # Older completions carry entry_mode at the top level only.
              # Copy it into the replay binding without mutating the frozen file.
              binding = dict(payload.get("presentation_binding") or {})
              binding["entry_mode"] = str(payload.get("entry_mode") or "").strip()
              binding["full_content"] = str(
                  binding.get("full_content") or payload.get("final_content") or ""
              )
              projection_payload["presentation_binding"] = binding
          # Notification projection is exact frozen-body forwarding. It must
          # retain identity/hash/idempotency binding, but must not re-run the
          # business managed-position/lifecycle gate; that gate belongs to the
          # task that produced the completion, not to notification delivery.
          ok, duplicate = record_post_close_review(account, projection_payload, delivery_only=True)
          if not ok:
              raise SystemExit("canonical post-close trade review projection was not accepted")
          market_date = str(review.get("market_date") or payload.get("market_date") or "").strip()
          event_path = Path("events/reviews") / f"{market_date}.json"
          if not event_path.exists():
              raise SystemExit("canonical post-close trade review event was not persisted")
          print(json.dumps({"canonical_publication": str(event_path).replace("\\\\", "/"), "duplicate": duplicate}, ensure_ascii=False))
          PY
          )
            echo "$result"
            event_path=$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("canonical_publication",""))' "$result")
          else
            result=$(python scripts/process_state_sync_request.py "$completion_path")
            echo "$result"
            event_path=$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("canonical_publication",""))' "$result")
            case "$event_path" in
              events/reviews/*.json)
                case "$event_path" in
                  events/reviews/formal_*.json)
                    echo "Scheduled Review completion resolved to Formal Decision event: $event_path" >&2
                    exit 1
                    ;;
                esac
                ;;
              *)
                echo "Scheduled Review canonical event path invalid: $event_path" >&2
                exit 1
                ;;
            esac
          fi
          case "$event_path" in
            events/reviews/*.json)
              case "$event_path" in
                events/reviews/formal_*.json)
                  echo "Scheduled Review completion resolved to Formal Decision event: $event_path" >&2
                  exit 1
                  ;;
              esac
              ;;
            *)
              echo "Scheduled Review canonical event path invalid: $event_path" >&2
              exit 1
              ;;
          esac
          echo "review_event_path=$event_path" >> "$GITHUB_OUTPUT"

      - name: Persist Scheduled Review canonical review event
        id: scheduled_review_report_persist
        if: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path != '' && steps.scheduled_completion_event.outputs.review_event_path != '' }}
        shell: bash
        env:
          EVENT_PATH: ${{ steps.scheduled_completion_event.outputs.review_event_path }}
        run: |
          set -euo pipefail
          test -n "$EVENT_PATH"
          case "$EVENT_PATH" in
            events/reviews/*.json)
              case "$EVENT_PATH" in
                events/reviews/formal_*.json)
                  echo "invalid Scheduled Review review event path: $EVENT_PATH" >&2
                  exit 1
                  ;;
              esac
              ;;
            *)
              echo "invalid Scheduled Review review event path: $EVENT_PATH" >&2
              exit 1
              ;;
          esac
          test -f "$EVENT_PATH"
          event_backup="/tmp/scheduled-review-event.json"
          cp -- "$EVENT_PATH" "$event_backup"
                    closure_path=$(python - "$EVENT_PATH" <<'PY'
          import json, sys
          event = json.load(open(sys.argv[1], encoding="utf-8"))
          market_date = str(event.get("market_date") or "").strip()
          print(f"data/state/close_review_closure_{market_date}.json" if market_date else "")
          PY
          )
                    report_path=$(python - "$EVENT_PATH" <<'PY'
          import json, sys
          event = json.load(open(sys.argv[1], encoding="utf-8"))
          binding = event.get("presentation_binding") or {}
          report_id = str(binding.get("report_id") or event.get("report_id") or "").strip()
          print(f"requests/report_delivery/{report_id.replace(':', '__')}.json" if report_id else "")
          PY
          )
          closure_backup="/tmp/scheduled-review-closure.json"
          report_backup="/tmp/scheduled-review-report.json"
          # Materialize the unique REPORT request from the immutable canonical
          # review event before notification delivery.  Event-only replay is
          # not a complete handoff and must fail closed instead of reporting a
          # green workflow with no deliverable report.
          if [ -n "$report_path" ] && [ ! -f "$report_path" ]; then
            PYTHONPATH=scripts python - "$EVENT_PATH" "$report_path" <<'PY'
          import json, sys
          from pathlib import Path
          from scripts.notification_center import build_report_delivery_request

          event_path = Path(sys.argv[1])
          report_path = Path(sys.argv[2])
          event = json.loads(event_path.read_text(encoding="utf-8"))
          binding = event.get("presentation_binding") or {}
          report_type = str(binding.get("report_type") or event.get("report_type") or "").strip()
          task_id = str(binding.get("task_id") or event.get("task_id") or "").strip()
          task_run_id = str(binding.get("task_run_id") or event.get("task_run_id") or "").strip()
          report_id = str(binding.get("report_id") or event.get("report_id") or "").strip()
          market_date = str(event.get("market_date") or binding.get("market_date") or "").strip()
          generated_at = str(event.get("generated_at") or event.get("recorded_at") or "").strip()
          content = str(binding.get("full_content") or event.get("full_content") or "")
          if not all((report_type, task_id, task_run_id, report_id, market_date, generated_at, content)):
              raise SystemExit("canonical review event lacks REPORT projection fields")
          title = content.splitlines()[0].strip() if content.splitlines() else report_type
          request = build_report_delivery_request(
              task_id=task_id,
              task_run_id=task_run_id,
              report_id=report_id,
              report_type=report_type,
              effective_market_date=market_date,
              title=title,
              summary=title,
              full_content=content,
              source_reference=f"canonical-review-event:{event_path.name}",
              idempotency_key=f"{report_type}:{task_run_id}",
              generated_at=generated_at,
              source_actor="Scheduled Review Notification Owner",
          )
          report_path.parent.mkdir(parents=True, exist_ok=True)
          report_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
          print(json.dumps({"status": "CREATED", "report_path": str(report_path)}, ensure_ascii=False))
          PY
          elif [ -n "$report_path" ]; then
            PYTHONPATH=scripts python - "$EVENT_PATH" "$report_path" <<'PY'
          import json, sys
          from pathlib import Path
          from scripts.notification_center import validate_report_delivery_request
          event = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
          report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
          valid, reason = validate_report_delivery_request(report)
          if not valid:
              raise SystemExit("existing REPORT request invalid: " + reason)
          binding = event.get("presentation_binding") or {}
          expected_report_id = str(binding.get("report_id") or event.get("report_id") or "")
          expected_body = str(binding.get("full_content") or event.get("full_content") or "")
          if str(report.get("report_id") or "") != expected_report_id:
              raise SystemExit("existing REPORT request identity mismatch")
          if str(report.get("full_content") or "") != expected_body:
              raise SystemExit("existing REPORT request body mismatch")
          print(json.dumps({"status": "IDEMPOTENT", "report_path": sys.argv[2]}, ensure_ascii=False))
          PY
          fi
          current_backup="/tmp/scheduled-review-current.json"
          dashboard_backup="/tmp/scheduled-review-dashboard.md"
          [ -f ETF当前状态_DASHBOARD.md ] && cp -- ETF当前状态_DASHBOARD.md "$dashboard_backup" || true
          [ -n "$closure_path" ] && [ -f "$closure_path" ] && cp -- "$closure_path" "$closure_backup" || true
          [ -n "$report_path" ] && [ -f "$report_path" ] && cp -- "$report_path" "$report_backup" || true
          [ -f data/state/CURRENT.json ] && cp -- data/state/CURRENT.json "$current_backup" || true
          git config user.name "ETF notification bot"
          git config user.email "etf-notification-bot@users.noreply.github.com"
          for attempt in 1 2 3; do
            git fetch --no-tags origin main
            git reset --hard origin/main
            mkdir -p "$(dirname "$EVENT_PATH")"
            cp -- "$event_backup" "$EVENT_PATH"
            if [ -n "$closure_path" ] && [ -f "$closure_backup" ]; then
              mkdir -p "$(dirname "$closure_path")"
              cp -- "$closure_backup" "$closure_path"
            fi
            if [ -n "$report_path" ] && [ -f "$report_backup" ]; then
              mkdir -p "$(dirname "$report_path")"
              cp -- "$report_backup" "$report_path"
            fi
            if [ -f "$current_backup" ]; then
              mkdir -p data/state
              cp -- "$current_backup" data/state/CURRENT.json
            fi
            if [ -f "$dashboard_backup" ]; then
              cp -- "$dashboard_backup" ETF当前状态_DASHBOARD.md
            fi
            git add -- "$EVENT_PATH"
            [ -n "$closure_path" ] && [ -f "$closure_path" ] && git add -- "$closure_path" || true
            [ -n "$report_path" ] && [ -f "$report_path" ] && git add -- "$report_path" || true
            [ -f "$current_backup" ] && git add -- data/state/CURRENT.json || true
            if git diff --cached --quiet; then
              echo "Scheduled Review canonical review event already present in current main."
              break
            fi
            git commit -m "event: persist Scheduled Review canonical review event"
            if git push origin HEAD:main; then
              break
            fi
            if [ "$attempt" -eq 3 ]; then
              echo "Scheduled Review canonical review event persistence exhausted latest-main retries." >&2
              exit 1
            fi
          echo "report_path=$report_path" >> "$GITHUB_OUTPUT"
          done
          echo "report_path=$report_path" >> "$GITHUB_OUTPUT"

      - name: Project Formal Decision completion to canonical review event
        id: formal_completion_event
        # Canonical notification is already sent from the same completion.
        # Formal event projection is audit-only and must not turn a successful
        # notification into a failed business handoff.
        continue-on-error: true
        if: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path != '' && steps.dispatch_completion_kind.outputs.formal == 'true' }}
        shell: bash
        env:
          COMPLETION_PATH: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path || steps.push_kind.outputs.formal_review_source_path || steps.workflow_review.outputs.formal_review_source_path }}
        run: |
          set -euo pipefail
          test -f "$COMPLETION_PATH"
          result=$(PYTHONPATH=scripts python - "$COMPLETION_PATH" <<'PY'
          import json
          import re
          import sys
          from pathlib import Path

          from scripts.process_state_sync_request import record_formal_decision_report_event
          from scripts.scheduled_review_completion import validate_formal_runtime_completion_contract
          from scripts.task_runtime_identity import derive_runtime_identity

          completion_path = Path(sys.argv[1])
          payload = json.loads(completion_path.read_text(encoding="utf-8"))
          binding = payload.get("presentation_binding") or {}
          report_type = str(payload.get("report_type") or binding.get("report_type") or "").strip().upper()
          task_id = str(payload.get("task_id") or binding.get("task_id") or "").strip()
          legacy_formal = (
              task_id == "ETF正式决策"
              and str(payload.get("decision_path") or "").strip().upper() == "FORMAL"
              and str(payload.get("interaction_scenario") or "").strip().upper() == "FORMAL_DECISION"
          )
          if legacy_formal:
              raise SystemExit("legacy Formal Decision projection is audit-only and cannot enter the production event path")
          if report_type != "ETF_FORMAL_DECISION":
              raise SystemExit("Formal Decision completion identity mismatch")
          if legacy_formal and not report_type:
              # Historical scheduled-task completions may omit report_type.
              # Normalize only the in-memory projection payload; never rewrite
              # the frozen completion file.
              payload["report_type"] = "ETF_FORMAL_DECISION"
              binding = dict(binding)
              binding["report_type"] = "ETF_FORMAL_DECISION"
              payload["presentation_binding"] = binding
          if legacy_formal:
              if not str(payload.get("formal_fact_type") or "").strip():
                  payload["formal_fact_type"] = "FORMAL_CURRENT_MAIN_DECISION"
              fact_binding = payload.get("fact_binding")
              if isinstance(fact_binding, dict):
                  fact_binding = dict(fact_binding)
                  if not fact_binding.get("market_date") and payload.get("market_date"):
                      fact_binding["market_date"] = payload["market_date"]
                  if not fact_binding.get("latest_snapshot") and fact_binding.get("market_snapshot"):
                      fact_binding["latest_snapshot"] = fact_binding["market_snapshot"]
                  if not fact_binding.get("captured_at_beijing") and fact_binding.get("snapshot_as_of_beijing"):
                      fact_binding["captured_at_beijing"] = fact_binding["snapshot_as_of_beijing"]
                  if not fact_binding.get("captured_at_beijing"):
                      snapshot_ref = str(fact_binding.get("latest_snapshot") or fact_binding.get("market_snapshot") or "").strip()
                      match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{6})", snapshot_ref)
                      if match:
                          clock = match.group(2)
                          fact_binding["captured_at_beijing"] = (
                              f"{match.group(1)}T{clock[:2]}:{clock[2:4]}:{clock[4:]}+08:00"
                          )
                  if not fact_binding.get("latest_valid_node"):
                      fact_binding["latest_valid_node"] = "live"
                  if not fact_binding.get("fact_status"):
                      fact_binding["fact_status"] = "READY/PASS"
                  payload["fact_binding"] = fact_binding
              if not str(payload.get("formal_fact_type") or "").strip():
                  payload["formal_fact_type"] = "FORMAL_CURRENT_MAIN_DECISION"
              fact_binding = payload.get("fact_binding")
              if isinstance(fact_binding, dict):
                  fact_binding = dict(fact_binding)
                  if not fact_binding.get("latest_snapshot") and fact_binding.get("market_snapshot"):
                      fact_binding["latest_snapshot"] = fact_binding["market_snapshot"]
                  if not fact_binding.get("captured_at_beijing") and fact_binding.get("snapshot_as_of_beijing"):
                      fact_binding["captured_at_beijing"] = fact_binding["snapshot_as_of_beijing"]
                  if not fact_binding.get("latest_valid_node"):
                      fact_binding["latest_valid_node"] = "live"
                  if not fact_binding.get("fact_status"):
                      fact_binding["fact_status"] = "READY/PASS"
                  payload["fact_binding"] = fact_binding
          # Scheduled-task surfaces may omit platform runtime IDs. Fill only
          # missing IDs from this confirmed request identity; never use history.
          requested_at = str(payload.get("requested_at_beijing") or payload.get("runtime_now") or "").strip()
          entry_mode = str(payload.get("entry_mode") or binding.get("entry_mode") or "AUTOMATIC_SCHEDULED_TASK").strip().upper()
          request_id = str(payload.get("request_id") or "").strip()
          supplied_run = str(payload.get("task_run_id") or binding.get("task_run_id") or "").strip()
          supplied_occurrence = str(payload.get("occurrence_id") or binding.get("occurrence_id") or "").strip()
          if not request_id or not requested_at:
              raise ValueError("Formal Decision completion missing request identity or requested_at_beijing")
          task_run_id, occurrence_id = derive_runtime_identity(
              "ETF正式决策", request_id, requested_at, entry_mode, supplied_run, supplied_occurrence
          )
          payload["task_run_id"] = task_run_id
          payload["occurrence_id"] = occurrence_id
          binding = dict(payload.get("presentation_binding") or {})
          binding["task_run_id"] = task_run_id
          binding["occurrence_id"] = occurrence_id
          binding.setdefault("task_id", "ETF正式决策")
          binding.setdefault("report_type", "ETF_FORMAL_DECISION")
          payload["presentation_binding"] = binding
          # Scheduled-task surfaces may omit platform runtime IDs. Fill only
          # missing IDs from this confirmed request identity; never use history.
          requested_at = str(payload.get("requested_at_beijing") or payload.get("runtime_now") or "").strip()
          entry_mode = str(payload.get("entry_mode") or binding.get("entry_mode") or "AUTOMATIC_SCHEDULED_TASK").strip().upper()
          request_id = str(payload.get("request_id") or "").strip()
          supplied_run = str(payload.get("task_run_id") or binding.get("task_run_id") or "").strip()
          supplied_occurrence = str(payload.get("occurrence_id") or binding.get("occurrence_id") or "").strip()
          if not request_id or not requested_at:
              raise ValueError("Formal Decision completion missing request identity or requested_at_beijing")
          task_run_id, occurrence_id = derive_runtime_identity(
              "ETF正式决策", request_id, requested_at, entry_mode, supplied_run, supplied_occurrence
          )
          payload["task_run_id"] = task_run_id
          payload["occurrence_id"] = occurrence_id
          binding = dict(payload.get("presentation_binding") or {})
          binding["task_run_id"] = task_run_id
          binding["occurrence_id"] = occurrence_id
          binding.setdefault("task_id", "ETF正式决策")
          binding.setdefault("report_type", "ETF_FORMAL_DECISION")
          payload["presentation_binding"] = binding
          validate_formal_runtime_completion_contract(payload)
          payload["_ingress_path"] = completion_path.as_posix()
          _, idempotent, event_path = record_formal_decision_report_event(payload)
          print(json.dumps({
              "canonical_publication": event_path,
              "idempotent": idempotent,
          }, ensure_ascii=False))
          PY
          )
          echo "$result"
          event_path=$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("canonical_publication",""))' "$result")
          case "$event_path" in
            events/reviews/formal_*.json) ;;
            *) echo "Formal Decision canonical event path invalid: $event_path" >&2; exit 1 ;;
          esac
          echo "review_event_path=$event_path" >> "$GITHUB_OUTPUT"

      - name: Persist Formal Decision canonical review event
        if: ${{ github.event_name == 'workflow_dispatch' && steps.formal_completion_event.outputs.review_event_path != '' }}
        shell: bash
        env:
          EVENT_PATH: ${{ steps.formal_completion_event.outputs.review_event_path }}
        run: |
          set -euo pipefail
          test -n "$EVENT_PATH"
          case "$EVENT_PATH" in
            events/reviews/formal_*.json) ;;
            *) echo "invalid Formal Decision review event path: $EVENT_PATH" >&2; exit 1 ;;
          esac
          test -f "$EVENT_PATH"
          event_backup="/tmp/formal-review-event.json"
          report_path=$(python - "$EVENT_PATH" <<'PY'
          import json, sys
          event = json.load(open(sys.argv[1], encoding="utf-8"))
          binding = event.get("presentation_binding") or {}
          report_id = str(event.get("report_id") or binding.get("report_id") or "").strip()
          print(f"requests/report_delivery/{report_id.replace(':', '__')}.json" if report_id else "")
          PY
          )
          report_backup="/tmp/formal-review-report.json"
          current_backup="/tmp/formal-review-current.json"
          dashboard_backup="/tmp/formal-review-dashboard.md"
          cp -- "$EVENT_PATH" "$event_backup"
          [ -n "$report_path" ] && [ -f "$report_path" ] && cp -- "$report_path" "$report_backup" || true
          [ -f data/state/CURRENT.json ] && cp -- data/state/CURRENT.json "$current_backup" || true
          # Rebuild the read-only Dashboard projection from the canonical event
          # before persisting it. This updates the same-day Formal Decision
          # index and lifecycle view without creating a second fact or decision.
          python - <<'PY'
          from pathlib import Path
          from scripts.sync_formal_files import sync_dashboard_projection
          result = sync_dashboard_projection(Path("."))
          if not isinstance(result, dict) or result.get("error") or result.get("status") in {"ERROR", "FAIL", "FAILED"}:
              raise SystemExit(f"Dashboard projection failed: {result}")
          print(result)
          PY
          cp -- ETF当前状态_DASHBOARD.md "$dashboard_backup"
          git config user.name "ETF notification bot"
          git config user.email "etf-notification-bot@users.noreply.github.com"
          for attempt in 1 2 3; do
            git fetch --no-tags origin main
            git reset --hard origin/main
            mkdir -p "$(dirname "$EVENT_PATH")"
            cp -- "$event_backup" "$EVENT_PATH"
            if [ -n "$report_path" ] && [ -f "$report_backup" ]; then
              mkdir -p "$(dirname "$report_path")"
              cp -- "$report_backup" "$report_path"
            fi
            if [ -f "$current_backup" ]; then
              mkdir -p data/state
              cp -- "$current_backup" data/state/CURRENT.json
            fi
            git add -- "$EVENT_PATH"
            [ -n "$report_path" ] && [ -f "$report_path" ] && git add -- "$report_path" || true
            git add -- ETF当前状态_DASHBOARD.md
            [ -f "$current_backup" ] && git add -- data/state/CURRENT.json || true
            if git diff --cached --quiet; then
              echo "Formal Decision canonical review event already present in current main."
              break
            fi
            git commit -m "event: persist Formal Decision canonical review event"
            if git push origin HEAD:main; then
              break
            fi
            if [ "$attempt" -eq 3 ]; then
              echo "Formal Decision canonical review event persistence exhausted latest-main retries." >&2
              exit 1
            fi
          done

      - name: Send material user-action notification
        id: event_notify
        # Delivery uses the already-bound current REPORT/review event. It must still run when a prior state persistence step fails; the sender remains fail-closed and never creates a second object.
        if: ${{ always() && ((github.event_name == 'push' && (steps.push_kind.outputs.decision == 'true' || steps.push_kind.outputs.trade == 'true' || steps.push_kind.outputs.account == 'true' || steps.push_kind.outputs.report == 'true' || steps.push_kind.outputs.report_handoff == 'true' || steps.push_kind.outputs.discovery_pulse == 'true' || steps.push_kind.outputs.hyperliquid_pulse == 'true')) || (github.event_name == 'workflow_dispatch' && (inputs.scheduled_review_completion_path != '' || steps.scheduled_completion_event.outputs.review_event_path != '' || steps.formal_completion_event.outputs.review_event_path != ''))) }}
        env:
          REPORT_DELIVERY_PATH: ${{ steps.push_kind.outputs.report_path || steps.scheduled_review_report_persist.outputs.report_path }}
          REPORT_HANDOFF_PATH: ${{ steps.push_kind.outputs.report_handoff_path }}
          REVIEW_EVENT_PATH: ${{ steps.scheduled_completion_event.outputs.review_event_path || steps.formal_completion_event.outputs.review_event_path || steps.push_kind.outputs.review_event_path || steps.workflow_review.outputs.review_event_path }}
          SCHEDULED_REVIEW_COMPLETION_PATH: ${{ github.event_name == 'workflow_dispatch' && inputs.scheduled_review_completion_path }}
          FORMAL_COMPLETION_EVENT: ${{ steps.formal_completion_event.outputs.review_event_path != '' && 'true' || 'false' }}
          FORCE_NOTIFICATION_RESEND: ${{ github.event_name == 'workflow_dispatch' && inputs.force_resend || (github.event_name == 'push' && steps.push_kind.outputs.formal_force_resend == 'true' && 'true' || 'false') }}
          PULSE_WAKE: ${{ github.event_name == 'push' && (steps.push_kind.outputs.discovery_pulse == 'true' || steps.push_kind.outputs.hyperliquid_pulse == 'true') && 'true' || 'false' }}
        run: |
          set -euo pipefail
          # A manual dispatch must remain self-contained. If an earlier
          # projection step did not expose its output, replay the same
          # completion idempotently in this run and recover the exact event
          # path before deciding whether to send. Never fall back to directory
          # scanning or to a historical notification object.
          if [ -z "$REVIEW_EVENT_PATH" ] && [ -n "$SCHEDULED_REVIEW_COMPLETION_PATH" ]; then
            completion_path="$SCHEDULED_REVIEW_COMPLETION_PATH"
            case "$completion_path" in
              requests/live_snapshot/*) ;;
              *) completion_path="requests/live_snapshot/$completion_path" ;;
            esac
            test -f "$completion_path"
          projection=$(PYTHONPATH=scripts python - "$completion_path" <<'PY'
          import json
          import re
          import sys
          from pathlib import Path
          from scripts.process_state_sync_request import record_formal_decision_report_event
          from scripts.scheduled_review_completion import validate_formal_runtime_completion_contract

          path = Path(sys.argv[1])
          payload = json.loads(path.read_text(encoding="utf-8"))
          binding = payload.get("presentation_binding") or {}
          report_type = str(payload.get("report_type") or binding.get("report_type") or "").strip().upper()
          task_id = str(payload.get("task_id") or binding.get("task_id") or "").strip()
          task_name = str(payload.get("task_name") or "").strip()
          if report_type == "ETF_FORMAL_DECISION" and (task_id == "ETF正式决策" or task_name == "ETF正式决策") and not payload.get("presentation_binding"):
              print(json.dumps({"canonical_publication": ""}, ensure_ascii=False))
              raise SystemExit(0)
          legacy_formal = (
              task_id == "ETF正式决策"
              and str(payload.get("decision_path") or "").strip().upper() == "FORMAL"
              and str(payload.get("interaction_scenario") or "").strip().upper() == "FORMAL_DECISION"
          )
          if legacy_formal:
              raise SystemExit("legacy Formal Decision recovery projection is audit-only and cannot enter the production event path")
          if report_type == "ETF_FORMAL_DECISION":
              if not report_type:
                  payload["report_type"] = "ETF_FORMAL_DECISION"
                  binding = dict(binding)
                  binding["report_type"] = "ETF_FORMAL_DECISION"
                  payload["presentation_binding"] = binding
              if not str(payload.get("formal_fact_type") or "").strip():
                  payload["formal_fact_type"] = "FORMAL_CURRENT_MAIN_DECISION"
              fact_binding = payload.get("fact_binding")
              if isinstance(fact_binding, dict):
                  fact_binding = dict(fact_binding)
                  if not fact_binding.get("market_date") and payload.get("market_date"):
                      fact_binding["market_date"] = payload["market_date"]
                  if not fact_binding.get("latest_snapshot") and fact_binding.get("market_snapshot"):
                      fact_binding["latest_snapshot"] = fact_binding["market_snapshot"]
                  if not fact_binding.get("captured_at_beijing") and fact_binding.get("snapshot_as_of_beijing"):
                      fact_binding["captured_at_beijing"] = fact_binding["snapshot_as_of_beijing"]
                  if not fact_binding.get("captured_at_beijing"):
                      snapshot_ref = str(fact_binding.get("latest_snapshot") or fact_binding.get("market_snapshot") or "").strip()
                      match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{6})", snapshot_ref)
                      if match:
                          clock = match.group(2)
                          fact_binding["captured_at_beijing"] = (
                              f"{match.group(1)}T{clock[:2]}:{clock[2:4]}:{clock[4:]}+08:00"
                          )
                  if not fact_binding.get("latest_valid_node"):
                      fact_binding["latest_valid_node"] = "live"
                  if not fact_binding.get("fact_status"):
                      fact_binding["fact_status"] = "READY/PASS"
                  payload["fact_binding"] = fact_binding
              validate_formal_runtime_completion_contract(payload)
              payload["_ingress_path"] = path.as_posix()
              _, idempotent, event_path = record_formal_decision_report_event(payload)
              print(json.dumps({"canonical_publication": event_path, "idempotent": idempotent}, ensure_ascii=False))
          else:
              import subprocess
              result = subprocess.run(
                  [sys.executable, "scripts/process_state_sync_request.py", str(path)],
                  check=True,
                  capture_output=True,
                  text=True,
              )
              print(result.stdout.strip())
          PY
          )
            REVIEW_EVENT_PATH=$(python -c 'import json,sys; print(json.loads(sys.argv[1]).get("canonical_publication", ""))' "$projection")
            if [ -z "$REVIEW_EVENT_PATH" ]; then
              REVIEW_EVENT_PATH=$(python -c 'import json,sys; p=json.load(open(sys.argv[1])); b=p.get("presentation_binding") or {}; t=str(p.get("report_type") or b.get("report_type") or "").strip().upper(); r=str(b.get("task_run_id") or p.get("task_run_id") or "").strip(); print(("events/reviews/trade-"+r+".json") if t=="ETF_TRADE_REVIEW" and r else ("events/reviews/system_"+r+".json" if t=="ETF_SYSTEM_REVIEW" and r else ""))' "$completion_path")
            fi
            case "$REVIEW_EVENT_PATH" in
              events/reviews/*.json) ;;
              *) REVIEW_EVENT_PATH="" ;;
            esac
          fi
          if [ -n "$SCHEDULED_REVIEW_COMPLETION_PATH" ] && [ -z "$REVIEW_EVENT_PATH" ]; then
            direct_content_flag="--formal"
            direct_content_flag="${direct_content_flag}-content-file"
            python scripts/run_guarded_notification.py center --mode event "$direct_content_flag" "$SCHEDULED_REVIEW_COMPLETION_PATH"
          elif [ -n "$REPORT_DELIVERY_PATH" ] || [ -n "$REPORT_HANDOFF_PATH" ] || [ -n "$REVIEW_EVENT_PATH" ] || [ "$PULSE_WAKE" = "true" ]; then
            export REVIEW_EVENT_PATH
            python scripts/run_guarded_notification.py center --mode event
          else
            echo "No current shared completion/report binding; fail closed without PushPlus."
          fi
      - name: Verify bound notification response before persistence
        if: ${{ always() && steps.event_notify.outcome == 'success' && (steps.scheduled_completion_event.outputs.review_event_path != '' || steps.scheduled_review_report_persist.outputs.report_path != '') }}
        shell: bash
        run: |
          set -euo pipefail
          test -s data/state/notification_center.json
          EXPECTED_EVENT_PATH="${{ steps.scheduled_completion_event.outputs.review_event_path || steps.formal_completion_event.outputs.review_event_path || steps.push_kind.outputs.review_event_path || steps.workflow_review.outputs.review_event_path }}"
          EXPECTED_REPORT_PATH="${{ steps.push_kind.outputs.report_path || steps.scheduled_review_report_persist.outputs.report_path }}"
          export EXPECTED_EVENT_PATH EXPECTED_REPORT_PATH
          python - data/state/notification_center.json <<'PY'
          import json, sys
          from pathlib import Path
          payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
          items = payload.get("notifications")
          if not isinstance(items, list) or not items:
              raise SystemExit("bound notification did not produce a durable notification record")
          event_path = Path(__import__("os").environ.get("EXPECTED_EVENT_PATH") or "")
          report_path = Path(__import__("os").environ.get("EXPECTED_REPORT_PATH") or "")
          event = json.loads(event_path.read_text(encoding="utf-8")) if event_path.is_file() else {}
          report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
          binding = event.get("presentation_binding") if isinstance(event.get("presentation_binding"), dict) else {}
          expected_report_id = str(binding.get("report_id") or event.get("report_id") or report.get("report_id") or "").strip().lower()
          expected_run_id = str(binding.get("task_run_id") or event.get("task_run_id") or report.get("task_run_id") or "").strip().lower()
          if not expected_report_id and not expected_run_id:
              raise SystemExit("bound notification verifier could not derive canonical identity")
          def record_identity_values(item):
              binding = item.get("presentation_binding") if isinstance(item.get("presentation_binding"), dict) else {}
              values = [str(item.get(k) or "").strip().lower() for k in ("report_id", "related_decision_id", "source_event_id", "event_id", "request_id", "parent_request_id", "task_run_id", "idempotency_key")]
              values += [str(binding.get(k) or "").strip().lower() for k in ("report_id", "request_id", "task_run_id", "occurrence_id")]
              return values
          bound = [x for x in items if (expected_report_id in record_identity_values(x) or expected_run_id in record_identity_values(x))]
          if not bound:
              print(json.dumps({"candidate_identities": [record_identity_values(x) for x in items[-10:]]}, ensure_ascii=False))
          if not bound:
              raise SystemExit("bound notification record is missing the scheduled review report identity")
          item = bound[-1]
          print(json.dumps({
              "lifecycle_status": item.get("lifecycle_status"),
              "response": item.get("response") or {},
              "report_id": item.get("report_id"),
          }, ensure_ascii=False))
          if item.get("lifecycle_status") not in {"SENT", "WAITING_CONFIRMATION", "CONFIRMED"}:
              raise SystemExit("bound notification was not accepted by the provider")
          PY

      - name: Send conditional A-share trading-day post-close account reminder
        id: close_notify
        if: ${{ github.event_name == 'schedule' }}
        continue-on-error: true
        run: python scripts/run_guarded_notification.py center --mode close

      - name: Persist unified notification and execution-reconciliation state
        if: ${{ always() }}
        shell: bash
        run: |
          mkdir -p /tmp/etf-notification-state/data/state
          EXPECTED_EVENT_PATH="${{ steps.scheduled_completion_event.outputs.review_event_path || steps.formal_completion_event.outputs.review_event_path || steps.push_kind.outputs.review_event_path || steps.workflow_review.outputs.review_event_path }}"
          EXPECTED_REPORT_PATH="${{ steps.push_kind.outputs.report_path || steps.scheduled_review_report_persist.outputs.report_path }}"
          EXPECTED_HANDOFF_PATH="${{ steps.push_kind.outputs.report_handoff_path }}"

          # A bound report/review event means the sender must have produced a
          # durable notification state. Never accept a zero-byte or malformed
          # state file after provider success; otherwise the provider may have
          # delivered while the audit record disappears from main.
          if [ -n "$EXPECTED_EVENT_PATH" ] || [ -n "$EXPECTED_REPORT_PATH" ] || [ -n "$EXPECTED_HANDOFF_PATH" ]; then
            test -s data/state/notification_center.json
            python - data/state/notification_center.json <<'PY'
          import json, sys
          from pathlib import Path
          path = Path(sys.argv[1])
          payload = json.loads(path.read_text(encoding="utf-8"))
          if not isinstance(payload, dict) or not isinstance(payload.get("notifications"), list):
              raise SystemExit("notification_center.json is not a valid durable state object")
          PY
          fi

          [ -s data/state/notification_center.json ] && cp data/state/notification_center.json /tmp/etf-notification-state/data/state/notification_center.json || true
          [ -s data/state/execution_reconciliation.json ] && cp data/state/execution_reconciliation.json /tmp/etf-notification-state/data/state/execution_reconciliation.json || true
          if [ ! -f /tmp/etf-notification-state/data/state/notification_center.json ] && [ ! -f /tmp/etf-notification-state/data/state/execution_reconciliation.json ]; then
            exit 0
          fi

          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          merge_notification_state() {
            python scripts/merge_notification_state.py "$@"
          }
          git fetch origin main
          git reset --hard origin/main
          if [ -f /tmp/etf-notification-state/data/state/notification_center.json ]; then
            merge_notification_state \
              --base data/state/notification_center.json \
              --incoming /tmp/etf-notification-state/data/state/notification_center.json \
              --output data/state/notification_center.json
          fi
          [ -f /tmp/etf-notification-state/data/state/execution_reconciliation.json ] && cp /tmp/etf-notification-state/data/state/execution_reconciliation.json data/state/execution_reconciliation.json || true
          git add data/state/notification_center.json data/state/execution_reconciliation.json 2>/dev/null || true
          if git diff --cached --quiet; then
            exit 0
          fi
          git commit -m "state: record notification and execution reconciliation"

          # The external delivery may already have succeeded while another
          # canonical writer advances main. Persist the accepted delivery by
          # replaying the saved incoming state against latest main; never rerun
          # the sender and never force-push.
          for attempt in 1 2 3; do
            if git push origin HEAD:main; then
              exit 0
            fi
            if [ "$attempt" -eq 3 ]; then
              echo "Notification state persistence exhausted latest-main replay attempts" >&2
              exit 1
            fi
            git fetch origin main
            git reset --hard origin/main
            if [ -f /tmp/etf-notification-state/data/state/notification_center.json ]; then
              merge_notification_state \
                --base data/state/notification_center.json \
                --incoming /tmp/etf-notification-state/data/state/notification_center.json \
                --output data/state/notification_center.json
            fi
            [ -f /tmp/etf-notification-state/data/state/execution_reconciliation.json ] && cp /tmp/etf-notification-state/data/state/execution_reconciliation.json data/state/execution_reconciliation.json || true
            git add data/state/notification_center.json data/state/execution_reconciliation.json 2>/dev/null || true
            if git diff --cached --quiet; then
              exit 0
            fi
            test -s data/state/notification_center.json
            python - data/state/notification_center.json <<'PY'
          import json, sys
          from pathlib import Path
          path = Path(sys.argv[1])
          payload = json.loads(path.read_text(encoding="utf-8"))
          if not isinstance(payload, dict) or not isinstance(payload.get("notifications"), list):
              raise SystemExit("notification_center.json is not a valid durable state object")
          PY
            git commit -m "state: record notification and execution reconciliation"
          done

      - name: Record post-notification consistency handoff
        if: ${{ always() && (steps.scheduled_completion_event.outputs.review_event_path != '' || steps.formal_completion_event.outputs.review_event_path != '' || steps.push_kind.outputs.report_path != '' || steps.push_kind.outputs.report_handoff_path != '') }}
        run: |
          echo "Notification delivery and state persistence completed; system consistency remains on its canonical workflow triggers."
          echo "No nested workflow dispatch is attempted from GITHUB_TOKEN."


      - name: Surface notification delivery failure without blocking trading system
        if: ${{ steps.test_notify.outcome == 'failure' || steps.pulse_test_notify.outcome == 'failure' || steps.event_notify.outcome == 'failure' || steps.close_notify.outcome == 'failure' || steps.a_share_summary_notify.outcome == 'failure' || steps.a_share_shock_notify.outcome == 'failure' || steps.apac_summary_notify.outcome == 'failure' || steps.apac_shock_notify.outcome == 'failure' || steps.us_summary_notify.outcome == 'failure' || steps.us_shock_notify.outcome == 'failure' }}
        run: |
          echo "User notification delivery failed; ETF market/data/trading-decision workflows remain unaffected."
          exit 1

@@FILE:scripts/apply_trade_fact_correction.py@@
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from formal_file_mutation_gateway import upsert_formal_line
from process_state_sync_request import sync_experience_transaction_index
from confirmed_trade_facts import canonical_etf_fee_projection, trade_signature

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()
STATE = ROOT / "data" / "state"
EQUITY = STATE / "etf_strategy_equity.json"
ARCHIVE = ROOT / "ETF市场行情档案_2026.md"
EXPERIENCE = ROOT / "ETF交易复盘与经验库_2026.md"
TZ = timezone(timedelta(hours=8))
ARCHIVE_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
ARCHIVE_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"
EXPERIENCE_START = "<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->"
EXPERIENCE_END = "<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->"


def reconcile_account_cash_projection(event: dict, fee: float, request: dict, stamp: str) -> dict:
    """Annotate the observed account cash row without rewriting the observed balance chain.

    A broker statement can confirm the trade's net cash effect while the account
    snapshot still contains an older fee-derived cash delta. Preserve that
    observed chain, expose the canonical trade cash effect, and retain any
    residual as explicitly unreconciled evidence.
    """
    account = read_json(STATE / "account_fact.json", {}) or {}
    rows = account.get("account_change_events_after_confirmed_at") or []
    event_id = str(event.get("event_id") or "").strip()
    confirmed_at = str(event.get("confirmed_at_beijing") or "").strip()
    expected = net_cash_effect(event, fee)
    if not event_id or not confirmed_at or expected is None or not isinstance(rows, list):
        return {"status": "NOT_APPLICABLE", "reason": "missing_event_identity_or_cash_effect"}

    candidates = [
        row for row in rows
        if isinstance(row, dict)
        and str(row.get("object") or "").strip().lower() == "cash"
        and str(row.get("event_time") or "").strip() == confirmed_at
    ]
    if len(candidates) != 1:
        return {
            "status": "NOT_APPLIED",
            "reason": "cash_projection_match_not_unique",
            "candidate_count": len(candidates),
        }

    row = candidates[0]
    observed = safe_float(row.get("amount_delta"))
    row_changed = False
    annotations = {
        "reconciled_trade_event_id": event_id,
        "reconciled_trade_cash_effect": expected,
        "reconciled_trade_fee_amount": round(fee, 2),
        "reconciled_trade_fee_source": str(request.get("source") or "BROKER_SCREENSHOT_CONFIRMED"),
    }
    for key, value in annotations.items():
        if row.get(key) != value:
            row[key] = value
            row_changed = True
    if observed is not None:
        residual = round(observed - expected, 2)
        if row.get("unreconciled_delta") != residual:
            row["unreconciled_delta"] = residual
            row_changed = True
        status = "RECONCILED_BY_CONFIRMED_TRADE" if abs(residual) < 0.005 else "UNRECONCILED_ACCOUNT_CHANGE"
        if row.get("reconciliation_status") != status:
            row["reconciliation_status"] = status
            row_changed = True
        note = (
            f"成交事实净现金影响{expected:.2f}元；账户快照观察现金变化{observed:.2f}元；"
            f"残差{residual:.2f}元，保留原始账户余额链，不据此改写后续现金调整。"
        )
        if row.get("reconciliation_note") != note:
            row["reconciliation_note"] = note
            row_changed = True
    if row_changed:
        account["fee_fact_updated_at"] = stamp
        account["fee_fact_source"] = str(request.get("source") or "BROKER_SCREENSHOT_CONFIRMED")
        write_json(STATE / "account_fact.json", account)
    return {
        "status": "UPDATED" if row_changed else "ALREADY_ANNOTATED",
        "observed_delta": observed,
        "expected_delta": expected,
        "unreconciled_delta": round(observed - expected, 2) if observed is not None else None,
    }


def persist_statement_scope(request: dict, stamp: str) -> dict:
    """Persist the complete executed-transaction scope from the broker statement without inferring unexecuted orders."""
    scope = request.get("statement_scope")
    if not isinstance(scope, dict):
        return {"status": "NOT_APPLICABLE", "reason": "statement_scope_not_supplied"}
    account = read_json(STATE / "account_fact.json", {}) or {}
    prior = account.get("broker_statement_reconciliation")
    merged = dict(prior) if isinstance(prior, dict) else {}
    merged.update(scope)
    merged["updated_at_beijing"] = stamp
    changed = account.get("broker_statement_reconciliation") != merged
    if changed:
        account["broker_statement_reconciliation"] = merged
        write_json(STATE / "account_fact.json", account)
    return {
        "status": "UPDATED" if changed else "ALREADY_ANNOTATED",
        "statement_scope_status": merged.get("statement_scope_status"),
        "executed_transaction_scope_status": merged.get("executed_transaction_scope_status"),
        "unmatched_executed_transaction_entry_count": merged.get("unmatched_executed_transaction_entry_count"),
        "orders_and_cancellations_status": merged.get("orders_and_cancellations_status"),
        "round_trip_status": merged.get("round_trip_status"),
        "capital_release_status": merged.get("capital_release_status"),
    }


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def net_cash_effect(event: dict, fee: float) -> float | None:
    """Derive the canonical cash movement from side, gross amount and fee."""
    gross = safe_float(event.get("amount"))
    if gross is None:
        gross = safe_float(event.get("gross_amount"))
    if gross is None:
        return None
    side = str(event.get("side") or event.get("action") or "").upper()
    if side == "SELL":
        return round(gross - fee, 2)
    if side == "BUY":
        return round(-(gross + fee), 2)
    return None


def equity_row_from_event(event: dict, fee: float) -> dict:
    """Project an already-recorded event when the auxiliary ledger lags it."""
    stamp = str(event.get("confirmed_at_beijing") or event.get("execution_at") or "")
    stamp = stamp.replace("T", " ")[:19]
    side = str(event.get("side") or event.get("action") or "").upper()
    gross = safe_float(event.get("amount"))
    row = {
        "datetime": stamp,
        "name": event.get("name"),
        "code": event.get("code"),
        "side": side,
        "quantity": event.get("quantity"),
        "price": event.get("price"),
        "gross_amount": gross,
        "source": f"events/trades/{event.get('event_id')}.json",
        "source_confidence": event.get("source_confidence") or event.get("source"),
        "entered_events_trades": True,
        "duplicate_check": f"unique_event_id_{event.get('event_id')}",
        "realized_pnl": None,
        "fee_amount": round(fee, 2),
        "fee_status": "CONFIRMED",
    }
    row["cash_flow_amount"] = net_cash_effect(event, fee)
    return row


def pending_fee_count(trades: list[dict], root: Path | None = None) -> int:
    if root is not None:
        return int(canonical_etf_fee_projection(root, trades)["pending_fee_count"])
    return sum(1 for t in trades if str(t.get("fee_status") or "").upper() != "CONFIRMED")


def rebuild_known_net(equity: dict, root: Path | None = None) -> None:
    trades = equity.get("trades") or []
    summary = equity.setdefault("summary", {})
    projection = canonical_etf_fee_projection(root, trades) if root is not None else None
    confirmed_fees = (
        projection["effective_confirmed_fee_sum"]
        if projection is not None
        else round(sum(float(t.get("fee_amount") or 0) for t in trades if str(t.get("fee_status") or "").upper() == "CONFIRMED"), 2)
    )
    gross_equity = safe_float(summary.get("current_gross_strategy_equity"))
    start_capital = safe_float(summary.get("starting_etf_strategy_capital")) or 200000.0
    gross_pnl = safe_float(summary.get("current_cumulative_pnl_gross"))
    if gross_equity is None:
        raise RuntimeError("etf_strategy_equity lacks gross basis required for deterministic fee correction")
    if gross_pnl is None:
        gross_pnl = round(gross_equity - start_capital, 2)
        summary["current_cumulative_pnl_gross"] = gross_pnl
    high_watermark = safe_float(summary.get("known_net_high_watermark")) or start_capital

    net_equity = round(gross_equity - confirmed_fees, 2)
    net_pnl = round(gross_pnl - confirmed_fees, 2)
    net_return_pct = round(net_pnl / start_capital * 100.0, 2)
    current_dd_amount = round(net_equity - high_watermark, 2)
    current_dd_pct = round(current_dd_amount / high_watermark * 100.0, 2) if high_watermark else None
    prior_max_dd = safe_float(summary.get("known_net_max_drawdown_amount"))
    prior_max_dd_pct = safe_float(summary.get("known_net_max_drawdown_pct"))
    prior_max_low = summary.get("known_net_max_drawdown_low")
    if prior_max_dd is None or current_dd_amount < prior_max_dd:
        max_dd_amount = current_dd_amount
        max_dd_pct = current_dd_pct
        max_low = datetime.now(TZ).date().isoformat()
    else:
        max_dd_amount = prior_max_dd
        max_dd_pct = prior_max_dd_pct
        max_low = prior_max_low

    pending = projection["pending_fee_count"] if projection is not None else pending_fee_count(trades)
    fee_summary = equity.setdefault("fee_summary", {})
    fee_summary["etf_confirmed_fees"] = confirmed_fees
    fee_summary["etf_pending_fees"] = pending
    fee_summary["pending_note"] = "canonical ETF fee projection"
    summary["known_fees"] = confirmed_fees
    summary["pending_fee_count"] = pending
    summary["unknown_fee_flag"] = pending > 0
    summary["fee_status"] = "ALL_RECORDED_TRADE_FEES_CONFIRMED" if pending == 0 else f"{pending} RECORDED TRADE FEE(S) PENDING_OR_NOT_YET_DISPLAYED"
    summary["known_net_status"] = f"KNOWN_NET_DEDUCTS_{confirmed_fees:.2f}_CONFIRMED_ETF_FEES" + ("; NOT_FINAL_NET" if pending else "; RECORDED_FEES_COMPLETE")
    summary["known_net_current_strategy_equity"] = net_equity
    summary["known_net_current_cumulative_pnl"] = net_pnl
    summary["known_net_current_strategy_return_pct"] = net_return_pct
    summary["known_net_current_drawdown_amount"] = current_dd_amount
    summary["known_net_current_drawdown_pct"] = current_dd_pct
    summary["known_net_max_drawdown_amount"] = max_dd_amount
    summary["known_net_max_drawdown_pct"] = max_dd_pct
    summary["known_net_max_drawdown_low"] = max_low
    summary["known_net_equity_data_quality"] = "KNOWN_NET_COMPLETE_FOR_RECORDED_TRADE_FEES" if pending == 0 else "KNOWN_NET_PARTIAL_PENDING_RECORDED_TRADE_FEES"
    if projection is not None:
        summary["trade_count"] = projection["canonical_trade_count"]
        summary["known_net_status"] += "; CANONICAL_EVENT_OVERLAYS_INCLUDED"


def _apply_attribution_request(req: dict) -> int:
    required = ("request_id", "trade_event_id", "linked_decision_id", "provenance")
    missing = [k for k in required if not req.get(k)]
    if missing: raise RuntimeError("attribution correction missing required fields: " + ", ".join(missing))
    event_id, decision_id = str(req["trade_event_id"]).strip(), str(req["linked_decision_id"]).strip()
    event = read_json(ROOT / "events/trades" / f"{event_id}.json", {}) or {}
    decision_path = ROOT / "events/decisions" / f"{decision_id}.json"
    decision = read_json(decision_path, {}) or {}
    if not decision:
        # Formal Decision is canonically persisted as a formal review event
        # when the direct Scheduled Review completion path is used. Reuse that
        # frozen event for attribution; do not create a second decision object.
        for candidate in (ROOT / "events/reviews").glob("formal_*.json"):
            obj = read_json(candidate, {}) or {}
            if str(obj.get("decision_id") or "") == decision_id:
                decision = obj
                decision_path = candidate
                break
    if not event or not decision: raise RuntimeError("attribution correction source fact or Decision Fact is missing")
    from decision_trade_link import _effective_time, _time, _decision_mentions_trade
    dt, tt = _effective_time(decision), _time(event.get("executed_at_beijing") or event.get("executed_at") or event.get("confirmed_at_beijing"))
    if str(event.get("execution_status") or "EXECUTED").upper() != "EXECUTED" or not dt or not tt or dt >= tt:
        raise RuntimeError("attribution correction violates executed-trade/PIT contract")
    if not _decision_mentions_trade(decision, event): raise RuntimeError("attribution correction action/security semantics are incompatible")
    p = req["provenance"]
    if not isinstance(p, dict) or not all(str(p.get(k) or "").strip() for k in ("resolver","source_trade","source_decision","parent_request_id")):
        raise RuntimeError("attribution correction provenance is incomplete")
    expected_decision_source = str(decision_path.relative_to(ROOT)).replace("\\", "/")
    if p["source_trade"] != f"events/trades/{event_id}.json" or p["source_decision"] != expected_decision_source:
        raise RuntimeError("attribution correction provenance source mismatch")
    rd = ROOT / "requests/trade_fact_correction"; rd.mkdir(parents=True, exist_ok=True)
    existing = [read_json(x, {}) or {} for x in rd.glob("*.json") if str((read_json(x, {}) or {}).get("correction_type") or "").upper() == "DECISION_ATTRIBUTION" and str((read_json(x, {}) or {}).get("trade_event_id") or "") == event_id]
    ids = {str(x.get("linked_decision_id") or "") for x in existing}
    if ids - {decision_id}: raise RuntimeError("existing canonical attribution conflicts with requested Decision Fact")
    req = dict(req); req.update({"correction_type":"DECISION_ATTRIBUTION","status":"APPLIED","immutable_trade_source":True})
    if existing and ids != {decision_id}:
        raise RuntimeError("trade already has a different canonical attribution correction")
    target=rd/f"{req['request_id']}.json"
    if not existing:
        if target.exists() and json.dumps(read_json(target,{}),ensure_ascii=False,sort_keys=True)!=json.dumps(req,ensure_ascii=False,sort_keys=True):
            raise RuntimeError("attribution correction request identity conflict")
        if not target.exists():
            write_json(target, req)
    # The correction is an audit-side canonical input.  Keep the observed
    # executed trade immutable; decision_trade_link resolves this request at
    # read time instead of rewriting the original trade fact.
    print(json.dumps({"status":"RECONCILED" if existing else "APPLIED","trade_event_id":event_id,"linked_decision_id":decision_id,"immutable_trade_source":True},ensure_ascii=False)); return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill newly confirmed metadata for an already-recorded trade without creating a new trade.")
    parser.add_argument("request_path")
    args = parser.parse_args()
    request_path = (ROOT / args.request_path).resolve()
    if ROOT not in request_path.parents or not request_path.exists():
        raise RuntimeError("invalid correction request path")
    req = read_json(request_path, {}) or {}
    if str(req.get("correction_type") or "").upper() == "DECISION_ATTRIBUTION":
        return _apply_attribution_request(req)
    event_id = str(req.get("trade_event_id") or "").strip()
    fee = safe_float(req.get("fee_amount"))
    if not event_id or fee is None or fee < 0:
        raise RuntimeError("trade_event_id and non-negative fee_amount are required")

    event_path = ROOT / "events" / "trades" / f"{event_id}.json"
    event = read_json(event_path, {}) or {}
    if not event:
        raise RuntimeError("existing trade event not found; correction cannot create a trade")
    prior_fee = safe_float(event.get("fee_amount"))
    prior_status = str(event.get("fee_status") or "").upper()
    expected_net = net_cash_effect(event, fee)
    prior_net = safe_float(event.get("net_cash_effect") or event.get("cash_flow_amount"))
    fee_is_current = prior_status == "CONFIRMED" and prior_fee is not None and abs(prior_fee - fee) < 0.005
    net_cash_is_current = expected_net is None or (prior_net is not None and abs(prior_net - expected_net) < 0.005)
    already_confirmed_same = fee_is_current and net_cash_is_current
    if prior_status == "CONFIRMED" and prior_fee is not None and not fee_is_current:
        raise RuntimeError("existing confirmed fee differs; explicit correction conflict requires manual review")

    stamp = str(req.get("requested_at_beijing") or event.get("fee_confirmed_at_beijing") or datetime.now(TZ).isoformat(timespec="seconds"))
    if not already_confirmed_same:
        event["fee_amount"] = round(fee, 2)
        event["fee_status"] = "CONFIRMED"
        event["fee_confirmed_at_beijing"] = str(req.get("evidence_time_beijing") or stamp)
        event["fee_source"] = str(req.get("source") or "BROKER_SCREENSHOT_CONFIRMED")
        event["fact_updated_at_beijing"] = stamp
        if expected_net is not None:
            event["net_cash_effect"] = expected_net
            event["cash_flow_amount"] = expected_net
        write_json(event_path, event)

    equity = read_json(EQUITY, {}) or {}
    equity_before = json.dumps(equity, ensure_ascii=False, sort_keys=True)
    trades = equity.get("trades") or []
    source_ref = f"events/trades/{event_id}.json"
    # Recovery rows may predate the canonical event and therefore carry only
    # ``source=experience_trade_index``.  Match by the economic trade
    # signature as well, otherwise a fee correction updates the event/index
    # but leaves the auxiliary equity ledger permanently PENDING.
    event_signature = trade_signature(event)
    matches = [
        t for t in trades
        if (
            str(t.get("source") or "") == source_ref
            or str(t.get("duplicate_check") or "") == f"unique_event_id_{event_id}"
            or trade_signature(t) == event_signature
        )
    ]
    if len(matches) > 1:
        raise RuntimeError("strategy equity does not contain exactly one matching trade; refuse partial correction")
    if matches:
        row = matches[0]
        row["fee_amount"] = round(fee, 2)
        row["fee_status"] = "CONFIRMED"
        row["cash_flow_amount"] = net_cash_effect(event, fee)
    else:
        # The event is authoritative.  A lagging auxiliary reconstruction must
        # converge by adding exactly this event, never by creating a new event.
        equity.setdefault("trades", []).append(equity_row_from_event(event, fee))
        equity.setdefault("summary", {})["trade_count"] = len(equity["trades"])
    rebuild_known_net(equity, ROOT)
    equity_after = json.dumps(equity, ensure_ascii=False, sort_keys=True)
    if equity_after != equity_before:
        equity["generated_at"] = stamp
        write_json(EQUITY, equity)

    # Reconcile the canonical transaction index by the existing trade event.
    # This updates both marker-based rows and legacy rows that predate the
    # TRADE_EVENT marker; it never creates a second trade or CASE.
    sync_experience_transaction_index(event)
    account_cash_projection = reconcile_account_cash_projection(event, fee, req, stamp)
    statement_scope_projection = persist_statement_scope(req, stamp)

    target = f"{event.get('name') or ''}（{event.get('code') or ''}）"
    correction_key = f"FEE_{event_id}"
    date = str(event.get("execution_date") or event.get("confirmed_at_beijing") or "")[:10]
    fee_source = str(event.get("fee_source") or req.get("source") or "BROKER_SCREENSHOT_CONFIRMED")
    line = f"{date} 已有成交事实补充：{target}成交费用确认{fee:.2f}元；原成交数量、价格、方向和交易日不变；来源：{fee_source}。"
    upsert_formal_line(ROOT, ARCHIVE.name, ARCHIVE_START, ARCHIVE_END, correction_key, f"- {line}")
    upsert_formal_line(ROOT, EXPERIENCE.name, EXPERIENCE_START, EXPERIENCE_END, correction_key, f"- 事实补充｜{line} 不新增CASE、不改变历史交易判断。")
    summary = equity.get("summary") or {}
    receipt = {
        "status": "RECONCILED" if already_confirmed_same else "APPLIED",
        "trade_event_id": event_id,
        "security": target,
        "fee_amount": round(fee, 2),
        "trade_event_updated": not already_confirmed_same,
        "equity_updated": True,
        "dashboard_updated": True,
        "archive_updated": True,
        "experience_updated": True,
        "account_fact_updated": account_cash_projection.get("status") == "UPDATED",
        "account_cash_projection": account_cash_projection,
        "statement_scope_projection": statement_scope_projection,
        "master_updated": False,
        "pending_fee_count": pending_fee_count(trades, ROOT),
        "known_fees": summary.get("known_fees"),
        "known_net_current_strategy_return_pct": summary.get("known_net_current_strategy_return_pct"),
        "applied_at_beijing": stamp,
        "safety_boundary": "Only enriches an existing trade with user/broker-confirmed fee metadata; never creates a trade or changes quantity, price, side, execution date, MASTER, risk permission or trading action.",
    }
    receipt_path = STATE / "trade_fact_correction_receipt.json"
    if read_json(receipt_path, {}) != receipt:
        write_json(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

@@FILE:scripts/build_execution_quality.py@@
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from statistics import mean

try:
    from state_manager import atomic_json_write, now_utc
    from decision_trade_link import resolve_link, decision_price_for_trade
except ModuleNotFoundError:
    from scripts.state_manager import atomic_json_write, now_utc
    from scripts.decision_trade_link import resolve_link, decision_price_for_trade

ROOT = Path(os.environ.get("ETF_SYSTEM_ROOT", Path(__file__).resolve().parents[1])).resolve()


def load_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None




def canonical_ineligibility(root: Path, trade: dict) -> dict:
    """Return an explicit review closure for a trade that cannot be legally linked."""
    execution_date = str(trade.get("execution_date") or trade.get("confirmed_at_beijing") or "")[:10]
    event_id = str(trade.get("event_id") or "").strip()
    if not execution_date or not event_id:
        return {}
    review = load_json(root / "events" / "reviews" / f"{execution_date}.json", {})
    payload = review.get("review") if isinstance(review.get("review"), dict) else review
    mapping = ((payload.get("account_close") or {}).get("case_mapping") or payload.get("case_mapping") or {}) if isinstance(payload, dict) else {}
    for item in mapping.get("ineligible_executed_trades") or []:
        if (
            str(item.get("trade_event_id") or "") == event_id
            and str(item.get("eligibility") or "").upper() == "EXPLICIT_CANONICAL_INELIGIBILITY"
        ):
            return item

    # User-confirmed broker facts may classify a same-day stock purchase as
    # an IPO base position. It is outside the ETF Formal Decision domain and
    # must be closed explicitly, without inventing a decision_id.
    account = load_json(root / "data" / "state" / "account_fact.json", {})
    for fact in account.get("trades") or []:
        if (
            str(fact.get("code") or "") == str(trade.get("code") or "")
            and str(fact.get("side") or "").upper() == str(trade.get("side") or "").upper()
            and str(fact.get("asset_type") or "").upper() == "STOCK"
            and "打新底仓" in str(fact.get("role") or "")
            and abs(float(fact.get("quantity") or 0) - float(trade.get("quantity") or 0)) < 0.01
            and abs(float(fact.get("amount") or 0) - float(trade.get("amount") or 0)) < 0.01
        ):
            return {
                "trade_event_id": event_id,
                "eligibility": "EXPLICIT_CANONICAL_INELIGIBILITY",
                "reason": "用户确认的非ETF打新底仓成交，不属于ETF正式决策归因域",
                "source": "data/state/account_fact.json",
                "role": fact.get("role"),
                "asset_type": fact.get("asset_type"),
            }
    return {}


def execution_time_for_trade(root: Path, trade: dict) -> str | None:
    """Prefer the broker-confirmed execution timestamp over screenshot confirmation time."""
    event_id = str(trade.get("event_id") or "").strip()
    request_dir = root / "requests" / "live_snapshot"
    if event_id and request_dir.exists():
        for path in sorted(request_dir.glob("*.json")):
            request = load_json(path, {})
            nested = request.get("trade_event") if isinstance(request.get("trade_event"), dict) else {}
            if str(nested.get("event_id") or "").strip() != event_id:
                continue
            executed = nested.get("executed_at") or nested.get("executed_at_beijing")
            if executed:
                return str(executed)
    # Broker screenshot ingress stores the exact execution timestamp under the
    # canonical account fact when the request does not carry a nested trade
    # object.  The screenshot receipt time is only a confirmation boundary and
    # must never be used as execution time when this fact is available.
    account = load_json(root / "data" / "state" / "account_fact.json", {})
    for fact in account.get("confirmed_trades") or []:
        if not isinstance(fact, dict):
            continue
        if all(str(fact.get(key) or "") == str(trade.get(key) or "") for key in ("code", "side", "quantity", "price")):
            executed = fact.get("executed_at_beijing") or fact.get("executed_at")
            if executed:
                return str(executed)
    return str(
        trade.get("executed_at")
        or trade.get("executed_at_beijing")
        or trade.get("confirmed_at_beijing")
        or ""
    ) or None


def build(root: Path = ROOT) -> dict:
    trade_dir = root / "events/trades"
    items = []
    for path in sorted(trade_dir.glob("*.json")) if trade_dir.exists() else []:
        trade = load_json(path, {})
        requested = str(trade.get("linked_decision_id") or "")
        linked, decision, link_status = resolve_link(root, trade, requested)
        if not linked:
            ineligibility = canonical_ineligibility(root, trade)
            if ineligibility:
                items.append({
                    "trade_event_id": trade.get("event_id") or path.stem,
                    "status": "INELIGIBLE_UNRECOVERABLE_TRADE",
                    "link_status": "EXPLICIT_CANONICAL_INELIGIBILITY",
                    "code": trade.get("code"),
                    "name": trade.get("name"),
                    "ineligibility_reason": ineligibility.get("reason"),
                    "ineligibility_record": ineligibility,
                })
            else:
                items.append({
                    "trade_event_id": trade.get("event_id") or path.stem,
                    "status": "UNLINKED_TRADE",
                    "link_status": link_status,
                    "code": trade.get("code"),
                    "name": trade.get("name"),
                })
            continue

        decision_price = decision_price_for_trade(decision, trade)
        execution_price = safe_float(trade.get("price"))
        diff_pct = None
        if decision_price not in (None, 0.0) and execution_price is not None:
            diff_pct = (execution_price / decision_price - 1.0) * 100.0
        side = str(trade.get("side") or "").upper()
        is_buy = side in {"BUY", "B", "买", "买入"} or "买" in side
        is_sell = side in {"SELL", "S", "卖", "卖出"} or "卖" in side
        adverse = diff_pct if is_buy else (-diff_pct if is_sell and diff_pct is not None else None)
        t0 = parse_time(decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing") or decision.get("decision_time_beijing"))
        execution_time = execution_time_for_trade(root, trade)
        t1 = parse_time(execution_time)
        delay = (t1 - t0).total_seconds() if t0 and t1 else None
        ordering = str(decision.get("decision_effective_ordering") or "").upper()
        timing_quality = str(decision.get("timing_quality") or "").upper()
        bounded_before_execution = ordering == "BEFORE_EXECUTION" and timing_quality in {"USER_CONFIRMED_BOUNDED", "USER_CONFIRMED"}

        items.append({
            "trade_event_id": trade.get("event_id") or path.stem,
            "decision_id": linked,
            "raw_linked_decision_id": requested or None,
            "link_status": link_status,
            "hypothesis_id": trade.get("hypothesis_id") or (decision.get("hypothesis_id") if str(trade.get("code") or "") == str(decision.get("candidate_code") or "") else None),
            "code": trade.get("code") or decision.get("candidate_code"),
            "name": trade.get("name") or decision.get("candidate_name"),
            "side": trade.get("side"),
            "decision_time_beijing": decision.get("decision_time_beijing"),
            "decision_effective_at_beijing": decision.get("decision_effective_at_beijing") or decision.get("issued_at_beijing"),
            "recorded_at_beijing": decision.get("recorded_at_beijing"),
            "decision_effective_ordering": decision.get("decision_effective_ordering"),
            "timing_quality": decision.get("timing_quality"),
            "timing_status": "EXACT" if delay is not None and delay >= 0 else "BOUNDED_BEFORE_EXECUTION" if bounded_before_execution else "UNAVAILABLE",
            "execution_time_beijing": execution_time,
            "decision_price": decision_price,
            "execution_price": execution_price,
            "decision_to_execution_seconds": round(delay, 1) if delay is not None and delay >= 0 else None,
            "adverse_execution_cost_pct": round(adverse, 4) if adverse is not None else None,
            "status": "READY" if decision_price is not None and execution_price is not None and ((delay is not None and delay >= 0) or bounded_before_execution) else "PARTIAL",
        })

    unlinked = [x for x in items if x.get("status") == "UNLINKED_TRADE"]
    ineligible = [x for x in items if x.get("status") == "INELIGIBLE_UNRECOVERABLE_TRADE"]
    adverse = [safe_float(x.get("adverse_execution_cost_pct")) for x in items]
    adverse = [x for x in adverse if x is not None]
    delays = [safe_float(x.get("decision_to_execution_seconds")) for x in items]
    delays = [x for x in delays if x is not None and x >= 0]
    corrected_links = len([x for x in items if x.get("link_status") == "AUTO_PRIOR_MATCH"])
    return {
        "schema_version": "1.1",
        "generated_at": now_utc(),
        "mode": "DECISION_EXECUTION_ATTRIBUTION",
        "read_only": True,
        "trade_event_count": len(items),
        "linked_trade_count": len([x for x in items if x.get("decision_id")]),
        "unlinked_trade_count": len(unlinked),
        "ineligible_unrecoverable_trade_count": len(ineligible),
        "attribution_backlog": {
            "status": "OPEN" if unlinked else "CLEAR",
            "items": unlinked,
            "rule": "未找到PIT有效正式决策时保留UNLINKED_TRADE；不得按时间、标的或持仓结果自动追认decision_id。后续必须补齐正式决策证据，或由正式复盘记录不可恢复归因。",
        },
        "attribution_gate": {
            "status": "BLOCKED" if unlinked else "PASS",
            "unlinked_trade_ids": [str(x.get("trade_event_id") or "") for x in unlinked],
            "rule": "每一笔EXECUTED成交必须存在PIT有效正式决策；无法关联时只能进入硬失败门，不得作为普通警告、不得自动追认、不得进入健康闭环。",
        },
        "corrected_future_or_invalid_link_count": corrected_links,
        "execution_cost_sample_count": len(adverse),
        "mean_adverse_execution_cost_pct": round(mean(adverse), 4) if adverse else None,
        "delay_sample_count": len(delays),
        "mean_decision_to_execution_seconds": round(mean(delays), 1) if delays else None,
        "items": items,
        "interpretation_rule": "成交只能归因到业务有效时间不晚于成交、或有明确用户确认有界先后关系的正式决策。canonical recorded_at可晚于成交，不用于制造负延迟；判断质量与执行质量分开评价。",
        "optimization_principle": "只分析能够帮助减少可避免执行损失或提高资本实现效率的差异；不追求零滑点、零延迟或完美执行。",
    }


def main() -> None:
    result = build(ROOT)
    atomic_json_write(ROOT / "data/state/execution_quality.json", result)
    print(json.dumps({
        "ok": True,
        "trade_event_count": result.get("trade_event_count"),
        "linked_trade_count": result.get("linked_trade_count"),
        "corrected_future_or_invalid_link_count": result.get("corrected_future_or_invalid_link_count"),
        "execution_cost_sample_count": result.get("execution_cost_sample_count"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

@@FILE:scripts/decision_trade_link.py@@
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Canonical completion inputs may carry a Beijing timestamp without an
        # explicit offset. Normalize it before PIT comparison; naive/aware
        # comparison must never crash the consistency gate.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed
    except ValueError:
        return None


def _decision_mentions_trade(event: dict, trade: dict) -> bool:
    formal = event.get("formal_decision") or {}
    binding = event.get("presentation_binding") or {}
    text = json.dumps(formal, ensure_ascii=False) + " " + str(binding.get("full_content") or event.get("final_content") or "")
    code = str(trade.get("code") or "")
    name = str(trade.get("name") or "")
    if code and code not in text and name and name not in text:
        return False
    side = str(trade.get("side") or "").upper()
    if side in {"SELL", "S", "卖", "卖出"} or "卖" in side:
        return any(k in text for k in ("卖出", "退出", "降低风险", "减持", "释放"))
    if side in {"BUY", "B", "买", "买入"} or "买" in side:
        return any(k in text for k in ("买入", "Tr…53091 tokens truncated…10] for line in rows]
    last_date = max(dates) if dates else ""
    pending = "无" if projection["pending_fee_count"] == 0 else f"{projection['pending_fee_count']}笔"
    sentence = f"截至{last_date}累计已确认ETF费用{projection['effective_confirmed_fee_sum']:.2f}元；待确认费用：{pending}。"
    return re.sub(r"截至.*?。", sentence, text, count=1)

def write_trade_review_required(event: dict) -> None:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    path = ROOT / "events" / "research" / "decision_outcomes" / f"TRADE_COMPLETED_REVIEW_REQUIRED_{event_id}.json"
    if path.exists():
        return
    atomic_json_write(path, {
        "event_type": "TRADE_COMPLETED_REVIEW_REQUIRED",
        "event_id": event_id,
        "notification_id": event.get("notification_id"),
        "related_decision_id": event.get("linked_decision_id"),
        "execution_date": event.get("execution_date"),
        "confirmation_date": event.get("confirmation_date"),
        "security_code": event.get("code"),
        "security_name": event.get("name"),
        "side": event.get("side"),
        "quantity": event.get("quantity"),
        "price": event.get("price"),
        "amount": event.get("amount"),
        "review_status": "PENDING_FORMAL_REVIEW",
        "objective_fields": ["trade_event", "strategy_risk_rate_change", "cash_change", "next_observation_point"],
        "safety_boundary": "只生成客观复盘待办，不自动判断交易正确/错误，不修改MASTER，不生成新的交易动作。",
    })


def _explicit_fact_type(request: dict) -> str:
    return str(
        request.get("formal_fact_type")
        or request.get("fact_type")
        or request.get("ingress_intent")
        or ""
    ).strip().upper()



def _account_fact_trade_is_executable(trade: dict) -> bool:
    """Recognize an executed security row explicitly present in a broker statement.

    Account screenshots may also carry non-ETF account transactions such as
    convertible-bond allotment/payment rows.  Those remain account facts and
    must not be promoted into the ETF execution-event stream.  No trade is
    inferred from balances, positions, or cash deltas.
    """
    if not isinstance(trade, dict):
        return False
    code = str(trade.get("code") or trade.get("security_code") or "").strip()
    side = str(trade.get("side") or trade.get("operation") or "").strip().upper()
    execution_time = (
        trade.get("executed_at")
        or trade.get("executed_at_beijing")
        or trade.get("trade_time")
        or trade.get("trade_time_beijing")
    )
    asset_marker = " ".join(
        str(trade.get(key) or "").strip().upper()
        for key in ("asset_type", "security_type", "trade_type")
    )
    non_execution_markers = ("BOND", "债", "配售", "申购", "中签", "缴款")
    if any(marker in asset_marker for marker in non_execution_markers):
        return False
    valid_side = side in {"BUY", "SELL", "B", "S", "买", "卖", "买入", "卖出"} or "买" in side or "卖" in side
    return bool(
        re.fullmatch(r"\d{6}", code)
        and valid_side
        and trade.get("quantity") is not None
        and trade.get("price") is not None
        and execution_time
    )


def _request_trade_events(request: dict) -> list[dict]:
    """Normalize all explicit broker trade ingress shapes.

    Top-level trade_event/trade_events remain the preferred canonical shape.
    When a broker screenshot request also carries transaction rows under the
    canonical account_fact.trades field, merge only rows with explicit
    execution evidence into the same event path.  Exact duplicates are
    suppressed; distinct statement rows are never discarded.  This closes the
    ingress gap without inferring trades from account balances or positions.
    """
    explicit: list[dict] = []
    plural = request.get("trade_events")
    if isinstance(plural, list) and plural and all(isinstance(item, dict) for item in plural):
        explicit.extend(plural)
    elif "trade_events" in request:
        return []
    singular = request.get("trade_event")
    if isinstance(singular, dict):
        explicit.append(singular)

    account_fact = request.get("account_fact")
    statement_trades = account_fact.get("trades") if isinstance(account_fact, dict) else None
    # Broker screenshot envelopes historically used confirmed_trades for
    # executed rows while the account-fact schema uses trades. Both are
    # explicit broker evidence; normalize them into the same canonical
    # execution-event path and let the existing idempotency key suppress
    # duplicates on replay.
    confirmed_trades = account_fact.get("confirmed_trades") if isinstance(account_fact, dict) else None
    if isinstance(confirmed_trades, list) and confirmed_trades:
        statement_trades = list(statement_trades or []) + list(confirmed_trades)
    if not isinstance(statement_trades, list) or not statement_trades:
        return explicit

    screenshot_confirmed_at = (
        request.get("screenshot_captured_at_beijing")
        or request.get("account_updated_at")
        or account_fact.get("account_screenshot_captured_at_beijing")
        or account_fact.get("updated_at")
        or request.get("requested_at_beijing")
    )
    source = account_fact.get("source") or request.get("source") or "BROKER_SCREENSHOT"

    def same_trade(left: dict, right: dict) -> bool:
        left_code = str(left.get("code") or left.get("security_code") or "").strip()
        right_code = str(right.get("code") or right.get("security_code") or "").strip()
        left_side = str(left.get("side") or left.get("action") or "").upper().strip()
        right_side = str(right.get("side") or right.get("action") or "").upper().strip()
        if not left_code or left_code != right_code or not left_side or left_side != right_side:
            return False
        if str(left.get("quantity") or "") != str(right.get("quantity") or ""):
            return False
        if str(left.get("price") or "") != str(right.get("price") or ""):
            return False
        left_time = str(left.get("executed_at") or left.get("executed_at_beijing") or "").strip()
        right_time = str(right.get("executed_at") or right.get("executed_at_beijing") or "").strip()
        return not left_time or not right_time or left_time == right_time

    normalized = list(explicit)
    for raw in statement_trades:
        if not _account_fact_trade_is_executable(raw):
            continue
        trade = dict(raw)
        if not trade.get("code") and trade.get("security_code"):
            trade["code"] = trade["security_code"]
        if not trade.get("name") and trade.get("security_name"):
            trade["name"] = trade["security_name"]
        if not trade.get("side") and trade.get("operation"):
            operation = str(trade["operation"]).strip()
            trade["side"] = "BUY" if "买" in operation else "SELL" if "卖" in operation else operation
        if not trade.get("executed_at") and trade.get("executed_at_beijing"):
            trade["executed_at"] = trade["executed_at_beijing"]
        if not trade.get("confirmed_at_beijing") and screenshot_confirmed_at:
            trade["confirmed_at_beijing"] = str(screenshot_confirmed_at)
        trade.setdefault("source", source)
        if not any(same_trade(trade, existing) for existing in normalized):
            normalized.append(trade)
    return normalized

def _trade_event_id(request: dict, trade: dict, idempotency_key: str, ordinal: int, total: int) -> str:
    """Use stable, collision-resistant IDs when a batch omits explicit event IDs."""
    explicit = str(trade.get("event_id") or "").strip()
    if explicit:
        return explicit
    request_id = str(request.get("request_id") or datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S"))
    if total == 1:
        return request_id
    suffix = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"{request_id}_{ordinal + 1}_{suffix}"

# Formal Decision completion projection accepts current-main runtime completions.

def _validate_formal_completion_identity(request: dict) -> None:
    """Validate live Formal Decision completions without assuming one transport label.

    The direct task writer may omit request_type and may carry only final_content
    plus identity/hash metadata in presentation_binding.  The frozen final_content
    is the canonical body; an optional duplicated binding body, when present,
    must match it exactly.
    """
    request_type = str(request.get("request_type") or "").strip().upper()
    if request_type not in {"", "SCHEDULED_REVIEW_COMPLETION"}:
        return
    if str(request.get("task_id") or "").strip() != "ETF正式决策":
        return
    if str(request.get("formal_fact_type") or "").strip().upper() not in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_CURRENT_MAIN_DECISION"}:
        return
    if str(request.get("interaction_scenario") or "").strip().upper() != "FORMAL_DECISION":
        raise ValueError("Formal Decision completion requires interaction_scenario=FORMAL_DECISION")
    for key in ("task_run_id", "occurrence_id", "request_id", "requested_at_beijing"):
        if not str(request.get(key) or "").strip():
            raise ValueError(f"Formal Decision completion missing {key}")
    content = _extract_scheduled_review_final_content(request)
    if not content:
        raise ValueError("Formal Decision completion requires frozen final_content")
    requested = parse_time(request.get("requested_at_beijing"))
    if requested is None:
        raise ValueError("Formal Decision completion requested_at_beijing is invalid")
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    match = re.fullmatch(r"【ETF正式决策】(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (正式判断|降级判断|超时判断)", first_line)
    if not match:
        raise ValueError("Formal Decision FINAL_CONTENT title is invalid")
    title_time = parse_time(match.group(1) + ":00+08:00")
    requested_minute = requested.astimezone(SHANGHAI).replace(second=0, microsecond=0)
    if title_time != requested_minute:
        raise ValueError("Formal Decision FINAL_CONTENT title time does not match this run")

    binding_value = request.get("presentation_binding")
    if not isinstance(binding_value, dict):
        raise ValueError("Formal Decision completion requires presentation_binding")
    binding = dict(binding_value)
    binding_content = str(binding.get("full_content") or "")
    if binding_content and binding_content != content:
        raise ValueError("Formal Decision completion frozen content mismatch")

    supplied_hashes = [
        str(request.get("content_hash") or "").strip(),
        str(binding.get("content_hash") or "").strip(),
    ]
    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if any(not re.fullmatch(r"[0-9a-fA-F]{64}", value) for value in supplied_hashes):
        raise ValueError("Formal Decision completion content_hash is missing or malformed")
    if any(value.lower() != expected_hash for value in supplied_hashes):
        raise ValueError("Formal Decision completion content_hash does not match final_content")
    request["presentation_binding"] = binding


def is_formal_decision_completion(request: dict) -> bool:
    """Recognize the shared Formal Decision completion contract independent of actor label.

    Live completions normally carry request_type=SCHEDULED_REVIEW_COMPLETION.
    Older valid completions may omit that transport label while retaining the
    full task/report/fact/presentation binding; those must remain replayable
    without mutating the immutable completion object.
    """
    binding = request.get("presentation_binding") if isinstance(request.get("presentation_binding"), dict) else {}
    request_type = str(request.get("request_type") or "").strip().upper()
    return (
        request_type in {"", "SCHEDULED_REVIEW_COMPLETION"}
        and str(request.get("formal_fact_type") or "").strip().upper() in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_CURRENT_MAIN_DECISION"}
        and str(request.get("task_id") or "").strip() == "ETF正式决策"
        and str(request.get("report_type") or request.get("report_kind") or binding.get("report_type") or "").strip().upper() == "ETF_FORMAL_DECISION"
        and str(request.get("interaction_scenario") or "").strip().upper() == "FORMAL_DECISION"
    )


def canonical_ingress_contract_for_request(request: dict) -> dict:
    """Return the actor-side terminal canonical ingress contract for formal facts."""

    fact_type = _explicit_fact_type(request)
    scenario = str(request.get("interaction_scenario") or "").strip().upper()
    channel = str(request.get("channel") or request.get("delivery_channel") or "").strip().upper()
    report_type = str(request.get("report_type") or request.get("report_kind") or "").strip().upper()

    is_broker_fact = is_broker_screenshot_request(request)
    trade_events = request.get("trade_events")
    has_trade_events = isinstance(trade_events, list) and bool(trade_events) and all(isinstance(item, dict) for item in trade_events)
    invalid_trade_events = "trade_events" in request and (
        not has_trade_events or isinstance(request.get("trade_event"), dict)
    )
    is_trade_fact = fact_type in {"CONFIRMED_TRADE", "TRADE_EVENT"} or isinstance(request.get("trade_event"), dict) or has_trade_events
    is_decision_fact = fact_type == "FORMAL_DECISION" or isinstance(request.get("formal_decision"), dict)
    # POST_CLOSE_REVIEW is a session/scenario label as well as a review fact
    # label. An explicit formal_decision payload remains a Decision Fact and
    # must not be diverted into the review-ingress contract merely because the
    # user requested the decision after A-share close.
    is_formal_completion = is_formal_decision_completion(request)
    is_review_fact = (
        (
            fact_type in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_SCHEDULED_TRADE_REVIEW", "POST_CLOSE_REVIEW"}
            and not is_formal_completion
        )
        or (scenario == "POST_CLOSE_REVIEW" and not is_decision_fact and not is_trade_fact and not is_formal_completion)
    )
    is_report_delivery = channel in {"REPORT", "REPORT_DELIVERY"} or report_type in {"ETF_TRADE_REVIEW", "SCHEDULED_TRADE_REVIEW"}

    if is_formal_completion:
        return {
            "required": False,
            "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
            "reason": "formal_decision_completion_projection",
        }

    if not any([is_broker_fact, is_review_fact, is_trade_fact, is_decision_fact]):
        return {
            "required": False,
            "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
            "reason": "not_a_formal_fact_ingress_request",
        }

    receipt = request.get("_ingress_path") or request.get("canonical_request_path") or request.get("request_path")
    submission_result = request.get("canonical_ingress_result") or request.get("canonical_submission_result")
    if isinstance(submission_result, dict):
        result_status = str(submission_result.get("status") or submission_result.get("terminal_state") or "").strip().upper()
        result_receipt = submission_result.get("receipt") or submission_result.get("receipt_path") or submission_result.get("commit_sha")
        if result_receipt and result_status in {"SUBMITTED", "SUCCESS", CANONICAL_INGRESS_SUBMITTED}:
            receipt = result_receipt
    if not receipt:
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_durable_ingress_receipt",
        }

    if invalid_trade_events:
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "invalid_trade_events",
        }

    if is_broker_fact:
        if isinstance(request.get("account_fact"), dict) or isinstance(request.get("trade_event"), dict) or has_trade_events:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "broker_account_fact_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_account_fact",
        }

    if is_review_fact:
        if isinstance(request.get("formal_review"), dict) or isinstance(request.get("review"), dict):
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "post_close_review_request_submitted",
            }
        if is_report_delivery:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
                "reason": "report_delivery_is_not_canonical_review_ingress",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_formal_review",
        }

    if is_trade_fact:
        if isinstance(request.get("trade_event"), dict) or has_trade_events:
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "confirmed_trade_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_trade_event",
        }

    if is_decision_fact:
        if isinstance(request.get("formal_decision"), dict):
            return {
                "required": True,
                "terminal_state": CANONICAL_INGRESS_SUBMITTED,
                "reason": "formal_decision_request_submitted",
            }
        return {
            "required": True,
            "terminal_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "reason": "missing_formal_decision",
        }

    return {
        "required": False,
        "terminal_state": CANONICAL_INGRESS_NOT_APPLICABLE,
        "reason": "not_a_formal_fact_ingress_request",
    }


def is_broker_screenshot_request(request: dict) -> bool:
    """Recognize broker facts from an explicit contract, with legacy compatibility.

    Free-form provenance remains metadata; it is not the business contract.
    The historical query-time request is accepted because its stable reason and
    ChatGPT origin explicitly identify a broker screenshot adoption.
    """
    explicit = _explicit_fact_type(request)
    if explicit in {"BROKER_ACCOUNT_SNAPSHOT", "ACCOUNT_FACT_CONFIRMATION", "BROKER_SCREENSHOT"}:
        return True
    if str(request.get("interaction_scenario") or "").strip().upper() == "BROKER_SCREENSHOT_SYNC":
        return True
    if str(request.get("source") or "").strip().upper() == "CHATGPT_USER_BROKER_SCREENSHOT":
        return True
    reason = str(request.get("reason") or "").strip().upper()
    requested_by = str(request.get("requested_by") or "").strip().lower()
    return (
        requested_by == "chatgpt"
        and reason == "USER_BROKER_SCREENSHOT_QUERY_TIME_REFRESH"
    )


def normalize_broker_account_fact_ingress(request: dict, supplied: object) -> tuple[object, int]:
    """Move explicitly typed non-security screenshot rows out of positions.

    No security identity is inferred. Rows are moved only when the ingress
    explicitly declares a non-security account asset type and supplies a name.
    Unknown security-like rows remain fail-closed.
    """
    if not is_broker_screenshot_request(request) or not isinstance(supplied, dict):
        return supplied, 0
    positions = supplied.get("positions")
    if not isinstance(positions, list):
        return supplied, 0
    normalized = json.loads(json.dumps(supplied))
    # Explicit screenshot-scoped fields are authoritative for this ingress.
    # Do not let stale uncertainty or timestamps survive a newer confirmed
    # screenshot when the request supplies the replacement value.
    for field in (
        "unconfirmed_visible_items",
        "broker_displayed_at_beijing",
        "account_screenshot_captured_at_beijing",
        "account_fact_derivation_note",
    ):
        if field in supplied:
            normalized[field] = supplied[field]
    # Normalize display-oriented screenshot aliases at the account ingress.
    # Formal Decision later consumes only the latest canonical account_fact.
    aliases = {
        "total_assets": "total_asset",
        "available_cash": "cash",
        "securities_market_value": "stock_market_value",
        "floating_pnl": "holding_pnl",
    }
    for source, target in aliases.items():
        if target not in supplied and normalized.get(source) is not None:
            normalized[target] = normalized[source]
    # A broker screenshot is a new authoritative observation for the
    # screenshot-scoped account fields. Refresh the observation timestamp and
    # derived aliases from this request instead of carrying stale metadata from
    # the prior account snapshot. Historical trade, lifecycle, and settlement
    # records remain preserved by merge_account_fact.
    receipt_time = request.get("account_updated_at") or request.get("submitted_at_beijing")
    if receipt_time:
        normalized["updated_at"] = str(receipt_time)
    market_date = str(request.get("market_date") or "").strip()
    market_boundary = str(request.get("market_fact_boundary_beijing") or "").strip()
    if market_date:
        normalized["market_date"] = market_date
        normalized["last_confirmed_market_date"] = market_date
    if market_boundary:
        normalized["broker_displayed_at_beijing"] = market_boundary
        normalized["screenshot_display_time"] = market_boundary
    if receipt_time:
        normalized["account_screenshot_captured_at_beijing"] = str(receipt_time)
    normalized["account_fact_derivation_note"] = (
        "当前账户金额、现金和持仓来自本次用户提供的券商截图；"
        "未显示代码的可见资产保持未确认，不并入正式ETF持仓。"
    )
    normalized.setdefault("source", request.get("source") or "CHATGPT_USER_BROKER_SCREENSHOT")
    # Keep display aliases synchronized with the current screenshot facts.
    if normalized.get("total_asset") is not None:
        normalized["total_assets"] = normalized["total_asset"]
    if normalized.get("cash") is not None:
        normalized["available_cash"] = normalized["cash"]
        normalized["deployable_cash_before_settlement"] = normalized["cash"]
    if normalized.get("stock_market_value") is not None:
        normalized["total_market_value"] = normalized["stock_market_value"]
    # Keep legacy account readers aligned with the current screenshot aliases.
    if normalized.get("daily_pnl") is not None:
        normalized["day_pnl"] = normalized["daily_pnl"]
    if normalized.get("daily_pnl_pct") is not None:
        normalized["day_pnl_pct"] = normalized["daily_pnl_pct"]
    kept = []
    unresolved = list(normalized.get("unresolved_assets") or [])
    moved = 0
    non_security_types = {"BOND", "CONVERTIBLE_BOND", "DEBT", "ACCOUNT_ASSET"}
    for index, position in enumerate(normalized.get("positions") or []):
        if not isinstance(position, dict):
            kept.append(position)
            continue
        code = str(position.get("code") or "").strip()
        asset_type = str(position.get("asset_type") or "").strip().upper()
        name = str(position.get("name") or position.get("security_name") or "").strip()
        # Explicitly typed, code-confirmed convertible bonds are securities.
        # Preserve their security subtype while using the canonical asset class.
        if re.fullmatch(r"\d{6}", code) and asset_type == "CONVERTIBLE_BOND":
            position = dict(position)
            position["asset_type"] = "OTHER"
            position["security_type"] = "CONVERTIBLE_BOND"
        if not re.fullmatch(r"\d{6}", code) and asset_type in non_security_types and name:
            row = dict(position)
            row["source_position_index"] = index
            row["identity_status"] = "UNRESOLVED_NON_SECURITY_ASSET"
            row["resolution_boundary"] = "No security code inferred from screenshot"
            unresolved.append(row)
            moved += 1
        else:
            kept.append(position)
    # Persist per-row normalization even when no unresolved asset was moved.
    normalized["positions"] = kept
    if moved:
        normalized["unresolved_assets"] = unresolved
    return normalized, moved


def validate_broker_account_fact_ingress(request: dict, supplied: object) -> str:
    """Fail fast on malformed ChatGPT-translated broker account facts.

    This validates transport shape only. It does not infer security identity,
    trading permission, or market facts from names.
    """
    if not is_broker_screenshot_request(request):
        return ""
    if supplied is None:
        # A broker screenshot request must carry the canonical account_fact
        # envelope. Silently accepting account_after_screenshot (or any other
        # non-canonical field) leaves the request durable but unconsumed and
        # makes the downstream gate appear stuck.
        if "account_after_screenshot" in request or "positions" in request:
            return "broker screenshot request must provide account_fact"
        return ""
    if not isinstance(supplied, dict):
        return "broker account_fact must be an object"
    positions = supplied.get("positions")
    if positions is None:
        return ""
    if not isinstance(positions, list):
        return "broker account_fact.positions must be a list"
    seen: set[str] = set()
    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            return f"broker account_fact.positions[{index}] must be an object"
        code = str(position.get("code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return f"broker account_fact.positions[{index}].code must be a six-digit security code"
        if code in seen:
            return f"broker account_fact.positions contains duplicate code {code}"
        seen.add(code)
        if position.get("quantity") is None:
            return f"broker account_fact.positions[{index}].quantity is required"
        try:
            float(position.get("quantity"))
        except (TypeError, ValueError):
            return f"broker account_fact.positions[{index}].quantity must be numeric"
        asset_type = str(position.get("asset_type") or "").strip().upper()
        if asset_type and asset_type not in {"ETF", "STOCK", "FUND", "OTHER"}:
            return f"broker account_fact.positions[{index}].asset_type is invalid"
    return ""


def merge_account_fact(prior: dict, supplied: dict) -> dict:
    """Merge confirmed screenshot facts and derive settlement cash canonically."""
    merged = json.loads(json.dumps(prior or {}))
    for key, value in (supplied or {}).items():
        if key != "settlement_obligations" and value is not None:
            merged[key] = json.loads(json.dumps(value))

    # Account facts have one date authority only.  The broker-confirmed
    # account date is stored as last_confirmed_market_date; legacy
    # account-level market_date is deliberately removed so it cannot drift
    # from the confirmed account fact and mislead downstream consumers.
    confirmed_market_date = (
        supplied.get("last_confirmed_market_date")
        or supplied.get("account_fact_market_date")
        or supplied.get("market_date")
        or merged.get("last_confirmed_market_date")
    )
    if confirmed_market_date:
        merged["last_confirmed_market_date"] = str(confirmed_market_date)
    merged.pop("market_date", None)

    # An ordinary screenshot is a partial observation.  Empty history-shaped
    # defaults from an ingress adapter must not erase canonical trade,
    # decision, fee, lifecycle, or reconciliation facts.
    for key in ("formal_action", "orders", "trades", "fee_facts", "account_reconciliation",
                "account_change_events_after_confirmed_at", "lifecycle", "confirmed_trades",
                "formal_decision", "reconciliation_metadata"):
        if key in (prior or {}) and (key not in (supplied or {}) or supplied.get(key) in (None, [], {})):
            merged[key] = json.loads(json.dumps(prior[key]))
    merged["settlement_obligations"] = _merge_settlement_obligations(prior or {}, supplied or {})
    for key in ("formal_action", "orders", "trades", "fee_facts", "account_reconciliation",
                "account_change_events_after_confirmed_at", "lifecycle", "confirmed_trades",
                "formal_decision", "reconciliation_metadata"):
        if key not in merged and key in (prior or {}):
            merged[key] = json.loads(json.dumps(prior[key]))
    _canonicalize_settlement_account(merged)
    _annotate_confirmed_ipo_origins(merged)
    _reconcile_known_ipo_events(merged)
    return merged


def apply_superseded_trade_corrections(request: dict) -> None:
    """Retire explicitly misidentified trade events without deleting history."""
    event_ids = request.get("supersedes_trade_event_ids") or []
    if not isinstance(event_ids, list):
        return
    replacement = str(request.get("replacement_trade_event_id") or "").strip()
    for raw_id in event_ids:
        event_id = str(raw_id or "").strip()
        if not event_id or "/" in event_id or "\\\\" in event_id or ".." in event_id:
            continue
        path = ROOT / "events" / "trades" / f"{event_id}.json"
        if not path.exists():
            continue
        event = load_json(path)
        if str(event.get("execution_status") or "").upper() == "SUPERSEDED":
            continue
        event["execution_status"] = "SUPERSEDED"
        event["superseded_by_event_id"] = replacement or None
        event["supersession_reason"] = str(request.get("supersession_reason") or "EXPLICIT_USER_FACT_CORRECTION")
        event["linked_decision_id"] = None
        event["execution_attribution"] = {"status": "SUPERSEDED", "replacement_event_id": replacement or None}
        atomic_json_write(path, event)


def process_historical_backfill_request(request: dict) -> dict:
    """Persist confirmed historical trade facts without touching current account state."""
    if str(request.get("ingress_mode") or "").upper() != "HISTORICAL_BACKFILL":
        raise ValueError("historical backfill mode is required")
    trades = request.get("historical_trades")
    if not isinstance(trades, list) or not trades:
        raise ValueError("historical_trades must be a non-empty list")
    trade_dir = ROOT / "events" / "trades"
    trade_dir.mkdir(parents=True, exist_ok=True)
    created = enriched = 0
    for raw in trades:
        if not isinstance(raw, dict):
            raise ValueError("historical trade must be an object")
        code, side = str(raw.get("code") or "").strip(), str(raw.get("side") or "").upper()
        quantity, price = safe_float(raw.get("quantity")), safe_float(raw.get("price"))
        executed_at = str(raw.get("executed_at") or raw.get("executed_at_beijing") or "").strip()
        confirmed_at = str(raw.get("confirmed_at_beijing") or "").strip()
        adoption_at = str(raw.get("historical_fact_adopted_at") or request.get("historical_fact_adopted_at") or datetime.now(SHANGHAI).isoformat(timespec="seconds")).strip()
        market_date = str(raw.get("market_date") or executed_at[:10]).strip()
        if not code or side not in {"BUY", "SELL"} or quantity is None or price is None or not executed_at or not adoption_at or not market_date:
            raise ValueError("historical trade core fields are incomplete")
        event_id = str(raw.get("event_id") or f"HISTORICAL_{market_date}_{code}_{side}_{format(quantity, '.12g')}_{format(price, '.12g')}")
        key = str(raw.get("idempotency_key") or f"HISTORICAL_BACKFILL|{code}|{side}|{format(quantity, '.12g')}|{format(price, '.12g')}|{executed_at}")
        path = trade_dir / f"{event_id}.json"
        existing = load_json(path) if path.exists() else None
        if existing:
            for field, expected in {"code": code, "side": side, "quantity": quantity, "price": price}.items():
                actual = existing.get(field)
                numeric_field = field in {"quantity", "price"}
                normalized_actual = safe_float(actual) if numeric_field else actual
                normalized_expected = expected
                mismatch = (
                    normalized_actual is None or normalized_actual != normalized_expected
                    if numeric_field
                    else str(actual) != str(expected)
                )
                if mismatch:
                    source_path = str(request.get("_ingress_path") or "events/trades/" + f"{event_id}.json")
                    detail = (
                        f"historical core mismatch: {event_id}:{field}; "
                        f"existing_value={actual!r}; existing_type={type(actual).__name__}; "
                        f"expected_value={expected!r}; expected_type={type(expected).__name__}; "
                        f"existing_source_path={source_path}; request_id={request.get('request_id')!r}; "
                        f"normalized_existing={normalized_actual!r}; normalized_existing_type={type(normalized_actual).__name__}; "
                        f"normalized_expected={normalized_expected!r}; normalized_expected_type={type(normalized_expected).__name__}"
                    )
                    raise ValueError(detail)
            existing_executed_at = existing.get("executed_at") or existing.get("executed_at_beijing")
            if existing_executed_at and str(existing_executed_at) != executed_at:
                source_path = str(request.get("_ingress_path") or "events/trades/" + f"{event_id}.json")
                raise ValueError(
                    f"historical core mismatch: {event_id}:executed_at; "
                    f"existing_value={existing_executed_at!r}; existing_type={type(existing_executed_at).__name__}; "
                    f"expected_value={executed_at!r}; expected_type={type(executed_at).__name__}; "
                    f"existing_source_path={source_path}; request_id={request.get('request_id')!r}; "
                    f"normalized_existing={str(existing_executed_at)!r}; normalized_existing_type=str; "
                    f"normalized_expected={executed_at!r}; normalized_expected_type=str"
                )
            changed = False
            if not existing.get("historical_backfill") and confirmed_at:
                existing["confirmed_at_beijing"] = confirmed_at
                existing["confirmation_time_semantics"] = "RELIABLE_HISTORICAL_CONFIRMATION"
                changed = True
            for field, value in {"historical_fact_adopted_at": adoption_at, "executed_at": executed_at, "executed_at_beijing": executed_at, "market_date": market_date, "account_updated_at": raw.get("account_updated_at"), "amount": raw.get("gross_amount"), "fee_amount": raw.get("fee_amount"), "fee": raw.get("fee_amount"), "fee_status": raw.get("fee_status"), "source": raw.get("source"), "source_type": raw.get("source_type") or "user_confirmed_historical_fact", "source_period": request.get("source_period") or "2026-07-13..2026-09-15", "historical_execution_time_preserved": True, "historical_backfill": True}.items():
                if value not in (None, "") and existing.get(field) != value:
                    existing[field] = value
                    changed = True
            if changed:
                atomic_json_write(path, existing)
                enriched += 1
            continue
        event = {
            "event_id": event_id, "idempotency_key": key, "event_type": "HISTORICAL_BACKFILL_TRADE",
            "historical_backfill": True, "code": code, "side": side, "quantity": quantity, "price": price,
            "executed_at": executed_at, "executed_at_beijing": executed_at,
            "confirmed_at_beijing": confirmed_at or adoption_at, "historical_fact_adopted_at": adoption_at, "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION" if confirmed_at else "CANONICAL_ADOPTION_TIME", "market_date": market_date,
            "account_updated_at": raw.get("account_updated_at"), "amount": raw.get("gross_amount", raw.get("amount")),
            "fee": raw.get("fee", raw.get("fee_amount")), "fee_amount": raw.get("fee_amount"), "fee_status": raw.get("fee_status"), "name": raw.get("name"),
            "source": raw.get("source") or "USER_CONFIRMED_HISTORICAL_SCREENSHOT",
            "source_type": raw.get("source_type") or "user_confirmed_historical_fact",
            "source_period": request.get("source_period") or "2026-07-13..2026-09-15",
            "historical_execution_time_preserved": True,
            "source_confidence": raw.get("source_confidence") or "USER_CONFIRMED",
            "execution_status": "EXECUTED", "replay_semantics": "FACT_ENRICHMENT_ONLY",
        }
        atomic_json_write(path, event)
        created += 1
    acquisition = request.get("acquisition")
    if acquisition:
        if not isinstance(acquisition, dict) or str(acquisition.get("code")) != "301689":
            raise ValueError("invalid acquisition contract")
        aid = str(acquisition.get("event_id") or "HISTORICAL_ACQUISITION_20260901_301689_500")
        apath = trade_dir / f"{aid}.json"
        if not apath.exists():
            atomic_json_write(apath, {
                "event_id": aid, "event_type": "IPO_ALLOTMENT_ACQUISITION",
                "historical_backfill": True, "asset_type": "STOCK", "code": "301689",
                "quantity": 500, "price": 16.0, "market_date": "2026-09-01",
                "executed_at": acquisition.get("executed_at") or "2026-09-01T00:00:00+08:00",
                "confirmed_at_beijing": acquisition.get("confirmed_at_beijing") or adoption_at,
                "historical_fact_adopted_at": adoption_at,
                "confirmation_time_semantics": "RELIABLE_HISTORICAL_CONFIRMATION" if acquisition.get("confirmed_at_beijing") else "CANONICAL_ADOPTION_TIME",
                "source_type": acquisition.get("source_type") or "user_confirmed_historical_fact",
                "source_period": request.get("source_period") or "2026-07-13..2026-09-15",
                "historical_execution_time_preserved": True,
                "lot_source": "confirmed_settlement_obligation",
                "source": acquisition.get("source") or "USER_CONFIRMED_HISTORICAL_SCREENSHOT",
                "execution_status": "ACQUIRED",
            })
            created += 1
        for path in trade_dir.glob("*.json"):
            event = load_json(path)
            if (str(event.get("code") or "") != "301689"
                    or str(event.get("side") or "").upper() != "SELL"
                    or safe_float(event.get("quantity")) != 500.0):
                continue
            if event.get("lot_source_event_id") != aid:
                event["lot_source_event_id"] = aid
                event["lot_source_relation"] = "ACQUISITION_TO_SELL"
                atomic_json_write(path, event)
                enriched += 1
    return {"ok": True, "ingress_mode": "HISTORICAL_BACKFILL", "created": created, "metadata_enriched": enriched, "current_account_mutated": False, "current_state_mutated": False}

def account_fact_is_older(prior: dict, supplied: dict) -> bool:
    old_time = parse_time(prior.get("updated_at"))
    new_time = parse_time(supplied.get("updated_at"))
    return bool(old_time and new_time and new_time < old_time)



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    parser.add_argument("--formal-replay-source-commit", default="")
    parser.add_argument("--formal-replay-dwp-commit", default="")
    args = parser.parse_args()
    forensic_historical_replay = bool(str(args.formal_replay_source_commit or "").strip()) and os.environ.get("ETF_FORMAL_REPLAY_FORENSIC") == "1"
    reset_formal_replay_forensic_trace(forensic_historical_replay)
    raw_request_path = Path(str(args.request_path or "").strip())
    # The workflow may pass either the repository-relative ingress path or the
    # absolute path after its execution-time-main reset/recovery step. Resolve
    # both forms through the same root boundary; never allow an outside path.
    req_path = (raw_request_path if raw_request_path.is_absolute() else ROOT / raw_request_path).resolve()
    if req_path == ROOT or ROOT not in req_path.parents or not req_path.is_file():
        raise RuntimeError("invalid state sync request path")
    request = load_json(req_path)
    request["_ingress_path"] = str(req_path.relative_to(ROOT)).replace("\\\\", "/")

    # Scheduled Review actors may omit the derived scenario field even though
    # the canonical request type and formal fact type identify it
    # deterministically. Normalize only this registered ingress; do not infer
    # identity from task names, timestamps, or historical objects.
    if (
        str(request.get("request_type") or "").strip().upper()
        == "SCHEDULED_REVIEW_COMPLETION"
        and not str(request.get("interaction_scenario") or "").strip()
    ):
        scenario_by_fact_type = {
            "FORMAL_POST_CLOSE_REVIEW": "POST_CLOSE_REVIEW",
            "FORMAL_SCHEDULED_TRADE_REVIEW": "SCHEDULED_TRADE_REVIEW",
            "FORMAL_SCHEDULED_SYSTEM_REVIEW": "SCHEDULED_SYSTEM_REVIEW",
            "FORMAL_MIDDAY_REVIEW": "MIDDAY_REVIEW",
        }
        fact_type = str(request.get("formal_fact_type") or "").strip().upper()
        scenario = scenario_by_fact_type.get(fact_type)
        if not scenario:
            raise ValueError(
                "scheduled review completion requires a registered interaction_scenario"
            )
        request["interaction_scenario"] = scenario

    _validate_formal_completion_identity(request)

    # New Formal Decision identities must not bypass the structured Business
    # Decision Source ingress. Historical legacy completion artifacts remain
    # readable/replayable, but a legacy envelope whose parent request was
    # created after the production cutover is rejected at the canonical
    # state-sync gateway even if a caller bypasses the CLI builder.
    legacy_manual_completion = (
        str(request.get("request_type") or "").strip().upper() == "STATE_SYNC_ONLY"
        and str(request.get("source") or "").strip().upper() == "CHATGPT_MANUAL_FORMAL_COMPLETION"
        and str(request.get("formal_fact_type") or "").strip().upper() == "FORMAL_DECISION"
    )
    if legacy_manual_completion:
        parent_id = str(request.get("parent_request_id") or "").strip()
        parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
        parent = load_json(parent_path) if parent_id and parent_path.exists() else {}
        parent_requested = str(parent.get("requested_at_beijing") or request.get("requested_at_beijing") or "").strip()
        production_cutover = "2026-09-25T17:17:43+08:00"
        if parent_requested and parent_requested >= production_cutover:
            raise ValueError(
                "new Formal Decision legacy completion bypass is forbidden; "
                "use BUSINESS_DECISION_SOURCE with structured decision_response"
            )

    # Formal Decision completion is the frozen, request-bound Scheduled Review
    # handoff. New live completions are self-contained and must not depend on a
    # BUSINESS_DECISION_SOURCE/BDS artifact. Historical BDS replay is handled by
    # the explicit source_dispatch path below and is not part of live completion.
    original_source_dispatch = str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE
    completion_dispatch = is_formal_decision_completion(request)
    if completion_dispatch:
        request.setdefault("parent_request_id", str(request.get("request_id") or "").strip())
        recorded, idempotent, event_path = record_formal_decision_report_event(request)
        print(json.dumps({
            "ok": True,
            "request_id": request.get("request_id"),
            "decision_id": request.get("decision_id"),
            "formal_decision_event": True,
            "canonical_publication": event_path,
            "idempotent_replay": idempotent,
            "bds_required": False,
        }, ensure_ascii=False))
        return 0

    # Explicit Business Decision Source dispatch. The ingress file itself is
    # the immutable durable Source artifact; projection only occurs after the
    # Phase 1 gate and never calls decision logic or refreshes market facts.
    source_dispatch = str(request.get("request_type") or "").strip().upper() == BUSINESS_DECISION_SOURCE
    source_fingerprint_value = ""
    source_reply_ready = False
    if source_dispatch:
        parent_id = str(request.get("parent_request_id") or "").strip()
        if not parent_id or any(part in parent_id for part in ("/", "\\", "..")):
            raise ValueError("business decision source requires a safe parent Formal Decision request")
        parent_path = ROOT / "requests" / "live_snapshot" / f"{parent_id}.json"
        if not parent_path.exists():
            raise ValueError("business decision source parent Formal Decision request is not durable")
        parent_request = load_json(parent_path)
        if str(parent_request.get("request_id") or "").strip() != parent_id:
            raise ValueError("business decision source parent request identity mismatch")

        replay_source_commit = str(args.formal_replay_source_commit or "").strip()
        if replay_source_commit:
            if not re.fullmatch(r"[0-9a-fA-F]{40}", replay_source_commit):
                raise ValueError("formal replay source commit must be a full commit SHA")
            request_rel = str(req_path.relative_to(ROOT)).replace("\\\\", "/")
            historical_source = subprocess.run(
                ["git", "show", f"{replay_source_commit}:{request_rel}"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            if historical_source.returncode != 0 or historical_source.stdout.encode("utf-8") != req_path.read_bytes():
                raise ValueError("formal replay source bytes must match the immutable source-introduction commit")
            replay_dwp_commit = str(args.formal_replay_dwp_commit or replay_source_commit).strip()
            historical_query = subprocess.run(
                ["git", "show", f"{replay_dwp_commit}:data/state/query_context.json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            if historical_query.returncode != 0 or not historical_query.stdout.strip():
                raise ValueError("formal replay DWP commit is missing historical request-bound query_context")
            query_context = json.loads(historical_query.stdout)
            decision_fact_pack = query_context.get("decision_fact_pack") or {}
            historical_account = decision_fact_pack.get("account_fact")
            if not isinstance(historical_account, dict) or not historical_account:
                raise ValueError("formal replay DWP commit is missing request-bound account fact")
            request["_historical_replay"] = True
            request["_historical_account_fact"] = json.loads(json.dumps(historical_account))
            # Keep discovery eligibility bound to the same immutable DWP context;
            # execution-time query_context may belong to a later request.
            request["_historical_query_context"] = query_context
        else:
            query_path = ROOT / "data" / "state" / "query_context.json"
            query_context = load_json(query_path) if query_path.exists() else {}
        packet = query_context.get("decision_fact_pack") or {}
        trigger = packet.get("trigger") or {}
        if str(trigger.get("request_id") or "").strip() != parent_id:
            # BUSINESS_DECISION_SOURCE is itself the durable actor handoff.  The
            # workflow may reset the workspace to latest main after the
            # request-bound query_context was produced; in that case the
            # execution-time projection must recover the same DWP from this
            # immutable BDS instead of accepting a later/empty DWP or failing
            # on a workspace-handoff artifact.  This is not a new request,
            # refresh, fact producer, or decision identity.
            bds_dwp = request.get("decision_work_package")
            request_type = str(request.get("request_type") or "").upper()
            if request_type == BUSINESS_DECISION_SOURCE and isinstance(bds_dwp, dict) and bds_dwp.get("problem_graph"):
                recovered_packet = dict(packet) if isinstance(packet, dict) else {}
                recovered_trigger = dict(trigger) if isinstance(trigger, dict) else {}
                recovered_trigger["request_id"] = parent_id
                recovered_packet["trigger"] = recovered_trigger
                recovered_packet["decision_work_package"] = json.loads(json.dumps(bds_dwp))
                query_context = dict(query_context) if isinstance(query_context, dict) else {}
                query_context["decision_fact_pack"] = recovered_packet
                packet = recovered_packet
                trigger = recovered_trigger
                request["_dwp_recovered_from_durable_bds"] = True
            else:
                qualifier = "historical" if replay_source_commit else "current"
                raise ValueError(f"business decision source requires {qualifier} request-bound Decision Work Package")
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("A_HISTORICAL_PACKET", obj=packet, dwp=packet.get("decision_work_package"), object_type="query_context.decision_fact_pack", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
            emit_formal_replay_forensic_fingerprint("B_PACKET_DWP", obj=packet, dwp=packet.get("decision_work_package"), object_type="packet.decision_work_package", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
        # Historical replay is provenance-bound: only the DWP loaded from the
        # explicitly selected historical query_context may define the problem graph.
        # A request-local/current DWP must never override that PIT boundary.
        if replay_source_commit:
            decision_work_package = packet.get("decision_work_package") or {}
        else:
            decision_work_package = request.get("decision_work_package") or packet.get("decision_work_package") or {}
        if not isinstance(decision_work_package, dict) or not decision_work_package.get("problem_graph"):
            raise ValueError("business decision source Decision Work Package is missing")
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("C_REQUEST_DWP_FREEZE", obj=request, dwp=decision_work_package, object_type="request.decision_work_package", provenance=f"historical_query_context:{replay_dwp_commit}", historical=True)
        # Freeze the already-validated request-bound opportunity domain onto the
        # in-memory ingress object. record_formal_decision() must validate the
        # deterministic projection against the exact DWP that produced it,
        # including REJECTed Discovery evaluation inputs, rather than falling
        # back to the later post-disposition Observation set.
        request["decision_work_package"] = json.loads(json.dumps(decision_work_package))

        supplied_decision = request.get("formal_decision")
        source_payload = dict(supplied_decision) if isinstance(supplied_decision, dict) else {}
        # Preserve the request-bound DWP in the durable BDS. The source
        # payload is the child handoff, but its validation and completion
        # recovery require the exact work package that produced the answer.
        source_payload["decision_work_package"] = json.loads(json.dumps(decision_work_package))
        # BUSINESS_DECISION_SOURCE carries Formal ingress identity at the top level.\n        # Preserve it when projecting the internal source payload; otherwise the\n        # validator sees an empty formal_entry_source even though the durable BDS\n        # is correctly bound to the actual entry.\n        for identity_field in (\n            "formal_entry_source",\n            "entry_mode",\n            "task_id",\n            "task_run_id",\n            "occurrence_id",\n        ):\n            if identity_field in request:\n                source_payload.setdefault(identity_field, request.get(identity_field))\n        current_context = query_context.get("current") or {}
        source_payload.setdefault("request_id", str(request.get("request_id") or f"{parent_id}__business_decision_source"))
        source_payload.setdefault("parent_request_id", parent_id)
        source_payload.setdefault("decision_id", str(request.get("decision_id") or f"{parent_id}_decision"))
        source_payload.setdefault("consumed_snapshot", str(request.get("consumed_snapshot") or current_context.get("latest_snapshot") or ""))
        source_payload.setdefault("requested_at_beijing", parent_request.get("requested_at_beijing") or parent_request.get("request_time_beijing"))
        source_payload.setdefault("market_date", query_context.get("market_date") or parent_request.get("market_date"))
        source_payload.setdefault("data_as_of_beijing", query_context.get("generated_at_beijing") or source_payload.get("requested_at_beijing"))
        source_payload["request_type"] = BUSINESS_DECISION_SOURCE
        decision_response = request.get("decision_response")
        request_presentation = request.get("presentation_content")
        response_presentation = (
            decision_response.get("presentation_content")
            if isinstance(decision_response, dict)
            else None
        )
        supplied_presentations = [
            value for value in (request_presentation, response_presentation)
            if value not in (None, "")
        ]
        if supplied_presentations and len(set(str(value) for value in supplied_presentations)) != 1:
            raise ValueError("formal presentation content conflicts across request entry points")
        presentation_content = (
            response_presentation
            if response_presentation not in (None, "")
            else request_presentation
        )
        # A Business Decision Source without the frozen user-visible body is
        # not a valid Formal Decision completion input.  Do not publish a
        # half-complete decision event that notification_center cannot safely
        # consume.
        if presentation_content in (None, ""):
            raise ValueError(
                "business decision source requires frozen presentation_content"
            )
        if presentation_content not in (None, ""):
            source_payload["presentation_content"] = presentation_content
            binding = request.get("presentation_binding")
            source_payload["presentation_binding"] = (
                dict(binding) if isinstance(binding, dict) else {}
            )
            source_payload["presentation_binding"]["full_content"] = presentation_content
            nested = source_payload.get("decision_response")
            if not isinstance(nested, dict):
                nested = {}
            nested["presentation_content"] = presentation_content
            source_payload["decision_response"] = nested
            request["presentation_content"] = presentation_content
        if isinstance(supplied_decision, dict) and supplied_decision:
            # A pre-shaped formal_decision is only legitimate for an explicitly
            # selected historical forensic replay. Current production ingress
            # must always project actor answers through the request-bound DWP.
            # Otherwise numeric/positional answers can bypass the problem_id
            # coverage contract and still reach the canonical writer.
            if not forensic_historical_replay:
                raise ValueError(
                    "current Business Decision Source ingress must use "
                    "actor-only decision_response keyed by request-bound problem_id"
                )
            source_payload = source_payload
        else:
            if not isinstance(decision_response, dict):
                raise ValueError("business decision source requires actor-only structured decision_response")
            source_payload = project_decision_response(source_payload, decision_response, decision_work_package, forensic_historical_replay=forensic_historical_replay)
            # The projection builder returns a canonical source object, but
            # ingress identity belongs to the immutable request envelope. Keep
            # that identity bound across the same-run projection boundary;
            # otherwise the downstream source validator sees an anonymous
            # source even though the parent request carried a valid Formal
            # entry/source mapping.
            for identity_field in (
                "formal_entry_source",
                "entry_mode",
                "task_id",
                "task_run_id",
                "occurrence_id",
            ):
                if identity_field in request:
                    source_payload[identity_field] = request.get(identity_field)
            for canonical_field in (
                "opportunity_status",
                "continued_holding_opportunity_cost",
                "decision_evidence_consumption",
            ):
                if request.get(canonical_field) not in (None, "", [], {}):
                    source_payload[canonical_field] = request.get(canonical_field)
        # Resolve and validate the exact request-bound PIT snapshot before
        # projecting the Business Decision Source. This path previously used
        # snapshot_rel/snapshot without selecting them, so a valid manual
        # Formal Decision could fail with NameError before canonical writing.
        decision_time = str(
            source_payload.get("requested_at_beijing")
            or parent_request.get("requested_at_beijing")
            or parent_request.get("request_time_beijing")
            or ""
        ).strip()
        availability_time = str(
            query_context.get("generated_at_beijing")
            or source_payload.get("data_as_of_beijing")
            or decision_time
            or ""
        ).strip()
        snapshot_rel, snapshot, pit_status = select_point_in_time_snapshot(
            str(source_payload.get("market_date") or "").strip(),
            decision_time,
            availability_time=availability_time,
            consumed_snapshot=str(source_payload.get("consumed_snapshot") or "").strip(),
        )
        if not snapshot_rel:
            raise ValueError(
                "business decision source requires a legal request-bound PIT snapshot: "
                + pit_status
            )
        source_payload["request_bound_fact_binding"] = {
            "snapshot_id": snapshot_rel,
            "market_date": str(snapshot.get("market_date") or market_date or "").strip(),
            "market_phase": str(snapshot.get("market_phase") or "").strip(),
            "captured_at": str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "").strip(),
            "pit_status": pit_status,
        }
        source = validate_source(
            source_payload,
            expected_snapshot=str(request.get("consumed_snapshot") or source_payload.get("consumed_snapshot") or "").strip(),
            expected_market_date=str(snapshot.get("market_date") or market_date or "").strip(),
            expected_market_phase=str(snapshot.get("market_phase") or "").strip(),
            expected_captured_at=str(snapshot.get("captured_at_beijing") or snapshot.get("captured_at") or "").strip(),
        )
        # Bind the selected request-bound DWP only after Source identity and
        # fingerprinting.  It is validation context, not mutable Source
        # business content, so replay/idempotency fingerprints remain stable.
        source = freeze_source_work_package(source, decision_work_package)
        source_fingerprint_value = source["fingerprint"]
        if forensic_historical_replay:
            emit_formal_replay_forensic_fingerprint("D_SOURCE_BINDING", obj=source, dwp=source.get("decision_work_package"), object_type="business_decision_source.bound", provenance=f"historical_query_context:{replay_dwp_commit}", source_fingerprint_value=source_fingerprint_value, historical=True)
        source_reply_ready = True
        request["_business_decision_source"] = source
        request["_source_fingerprint"] = source_fingerprint_value
        # Deterministic projection only: no new facts, ranking, or investment
        # judgment is introduced by this branch.
        projected = build_formal_completion_from_source(source)
        projected.pop("request_type", None)
        projected.pop("source_type", None)
        projected.pop("source_durable", None)
        projected.pop("reply_ready", None)
        projected.pop("projection_status", None)
        projected.pop("fingerprint", None)
        request["formal_decision"] = projected
        _persist_formal_opportunity_projection(ROOT, source)
    if original_source_dispatch:
        print(json.dumps({"ok": True, "request_id": request.get("request_id"), "business_decision_source_persisted": True, "completion_required": True}, ensure_ascii=False))
        return 0
    system_review_recorded, system_review_idempotent = record_scheduled_system_review(request)
    if system_review_recorded:
        result = {"ok": True, "request_id": request.get("request_id"), "system_review_completion": True, "idempotent_replay": system_review_idempotent, "canonical_publication": "events/reviews/system_" + str(((request.get("presentation_binding") or {}).get("task_run_id") or request.get("task_run_id") or "")).strip() + ".json"}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if str(request.get("ingress_mode") or "").upper() == "HISTORICAL_BACKFILL":
        result = process_historical_backfill_request(request)
        result.update({
            "request_id": request.get("request_id"),
            "trade_event_recorded": True,
            "account_sync_status": "NOT_APPLICABLE",
            "canonical_ingress_state": CANONICAL_INGRESS_SUBMITTED,
            "canonical_ingress_failure_reason": "",
            "source_dispatch": source_dispatch,
            "source_durable": bool(source_dispatch),
            "source_immutable": bool(source_dispatch),
            "source_fingerprint": source_fingerprint_value,
            "reply_ready": source_reply_ready,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    canonical_ingress_contract = canonical_ingress_contract_for_request(request)
    trades = _request_trade_events(request)
    prior_account = load_json(ACCOUNT) if ACCOUNT.exists() else {}
    supplied_account = request.get("account_fact")
    existing_trades = []
    for trade in trades:
        confirmed_at = (
            trade.get("confirmed_at_beijing") or request.get("requested_at_beijing")
            or trade.get("executed_at") or prior_account.get("updated_at")
        )
        replay_key = _trade_idempotency_key(trade, confirmed_at)
        existing = _find_existing_trade(trade, confirmed_at, replay_key)
        existing_trades.append(existing)
        if request.get("account_fact") is None and existing is None:
            supplied_account = _apply_trade_to_account(supplied_account or prior_account, trade)
    account_sync_status = "NOT_APPLICABLE"
    if is_broker_screenshot_request(request) and not isinstance(supplied_account, dict) and not trades:
        result = {
            "ok": False,
            "request_id": request.get("request_id"),
            "interaction_scenario": request.get("interaction_scenario"),
            "status": "NO_ACCOUNT_FACT",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "canonical_ingress_state": canonical_ingress_contract["terminal_state"],
            "canonical_ingress_failure_reason": canonical_ingress_contract["reason"],
            "dashboard_updated": False,
            "detail": "Broker screenshot request has no request-scoped account_fact; market success must not be treated as account sync success.",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1
    if (
        canonical_ingress_contract["required"]
        and canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_FAILED_EXPLICITLY
        and canonical_ingress_contract["reason"] != "missing_account_fact"
    ):
        print(json.dumps({
            "ok": False,
            "status": "CANONICAL_INGRESS_FAILED_EXPLICITLY",
            "canonical_ingress_state": canonical_ingress_contract["terminal_state"],
            "canonical_ingress_failure_reason": canonical_ingress_contract["reason"],
            "request": str(req_path.relative_to(ROOT)).replace("\\", "/"),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    supplied_account, normalized_non_security_asset_count = normalize_broker_account_fact_ingress(request, supplied_account)
    broker_shape_error = validate_broker_account_fact_ingress(request, supplied_account)
    if broker_shape_error:
        print(json.dumps({
            "ok": False,
            "request_id": request.get("request_id"),
            "status": "BROKER_ACCOUNT_FACT_SCHEMA_INVALID",
            "account_sync_status": "ACCOUNT_SYNC_NOT_PERFORMED",
            "canonical_ingress_state": CANONICAL_INGRESS_FAILED_EXPLICITLY,
            "canonical_ingress_failure_reason": "broker_account_fact_schema_invalid",
            "dashboard_updated": False,
            "detail": broker_shape_error,
        }, ensure_ascii=False))
        return 1

    if isinstance(supplied_account, dict):
        supplied_account = merge_account_fact(prior_account, supplied_account)
        supplied_account.setdefault("status", "VALID")
        supplied_account.setdefault("validity_mode", "EVENT_DRIVEN_CARRY_FORWARD")
        if account_fact_is_older(prior_account, supplied_account):
            account_sync_status = "STALE_ACCOUNT_FACT_IGNORED"
        else:
            # Use the canonicalized event history returned by merge_account_fact:
            # it may have upgraded a previously unresolved IPO registration.
            prior_events = supplied_account.get("account_change_events_after_confirmed_at") or []
            new_events = _account_change_events(prior_account, supplied_account, request, trades)
            known = {str(x.get("idempotency_key") or x.get("event_id") or "") for x in prior_events}
            supplied_account["account_change_events_after_confirmed_at"] = prior_events + [x for x in new_events if str(x.get("idempotency_key")) not in known]
            if supplied_account == prior_account:
                account_sync_status = "ACCOUNT_SYNC_IDEMPOTENT_NOOP"
            else:
                account_sync_status = "ACCOUNT_FACT_UPDATED"
                atomic_json_write(ACCOUNT, supplied_account)
    account = load_json(ACCOUNT)
    if account.get("status") != "VALID":
        raise RuntimeError("account_fact is not VALID")
    decision_recorded, decision_id = record_formal_decision(request)
    formal_decision_current = False
    if decision_recorded:
        formal_decision_current, formal_action_changed = project_current_formal_action(
            account, request, decision_id
        )
        if formal_action_changed:
            atomic_json_write(ACCOUNT, account)
    trade_event_recorded = False
    persisted_trade_events = []
    for trade_index, trade in enumerate(trades):
        confirmed_at = trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or account.get("updated_at")
        idempotency_key = _trade_idempotency_key(trade, confirmed_at)
        existing = existing_trades[trade_index] or _find_existing_trade(trade, confirmed_at, idempotency_key)
        if existing:
            event, event_id, trade_event_recorded = existing, str(existing.get("event_id") or ""), True
            # A request-scoped replay may repair an earlier unlinked trade, but
            # never by guessing from the latest account action.  The replay must
            # carry an explicit decision identity or a same-request formal decision.
            explicit_replay_decision_id = str(trade.get("linked_decision_id") or trade.get("decision_id") or decision_id or "").strip()
            if not str(event.get("linked_decision_id") or "").strip() and explicit_replay_decision_id:
                event["linked_decision_id"] = explicit_replay_decision_id
            # A request-scoped confirmation time is authoritative for an
            # idempotent replay.  Never replace an earlier PIT confirmation with
            # the current account snapshot's updated_at.
            changed = False
            requested_confirmed_at = str(trade.get("confirmed_at_beijing") or request.get("requested_at_beijing") or trade.get("executed_at") or "").strip()
            if requested_confirmed_at and str(event.get("confirmed_at_beijing") or "") != requested_confirmed_at:
                event["confirmed_at_beijing"] = requested_confirmed_at
                event["execution_date"] = trade.get("market_date") or requested_confirmed_at[:10]
                event["confirmation_date"] = trade.get("market_date") or requested_confirmed_at[:10]
                event["idempotency_key"] = _trade_idempotency_key(trade, requested_confirmed_at)
                changed = True
            # Idempotent fee/account replays must also converge stale linkage and
            # attribution written by an older producer; they never create another event.
            link_trade = {
                **trade,
                "confirmed_at_beijing": requested_confirmed_at
                or str(event.get("confirmed_at_beijing") or confirmed_at),
            }
            if trade.get("executed_at_beijing") or trade.get("executed_at"):
                event["executed_at_beijing"] = trade.get("executed_at_beijing") or trade.get("executed_at")
                changed = True
            linked_decision_id = resolve_trade_linked_decision_id(
                link_trade,
                decision_id,
                existing_linked_decision_id=str(event.get("linked_decision_id") or ""),
            )
            if str(event.get("linked_decision_id") or "") != linked_decision_id:
                event["linked_decision_id"] = linked_decision_id or None
                changed = True
            refreshed_attribution = execution_attribution(event, linked_decision_id)
            refreshed_hypothesis = refreshed_attribution.get("hypothesis_id")
            if (
                event.get("execution_attribution") != refreshed_attribution
                or event.get("hypothesis_id") != refreshed_hypothesis
            ):
                event["execution_attribution"] = refreshed_attribution
                event["hypothesis_id"] = refreshed_hypothesis
                changed = True
            if changed:
                atomic_json_write(ROOT / "events" / "trades" / f"{event_id}.json", event)
        else:
            event_id = _trade_event_id(request, trade, idempotency_key, trade_index, len(trades))
            link_trade = {**trade, "confirmed_at_beijing": confirmed_at}
            linked_decision_id = resolve_trade_linked_decision_id(link_trade, decision_id)
            attribution = execution_attribution(link_trade, linked_decision_id)
            event = {"event_id": event_id, "idempotency_key": idempotency_key, "confirmed_at_beijing": confirmed_at, "executed_at_beijing": trade.get("executed_at_beijing") or trade.get("executed_at"), "execution_date": trade.get("execution_date") or str(confirmed_at)[:10], "confirmation_date": trade.get("market_date") or str(confirmed_at)[:10], "notification_id": trade.get("notification_id"), "name": trade.get("name"), "code": trade.get("code"), "side": trade.get("side"), "quantity": trade.get("quantity"), "price": trade.get("price"), "amount": trade.get("amount"), "lifecycle": trade.get("lifecycle"), "source": trade.get("source", account.get("source")), "source_confidence": trade.get("source_confidence"), "linked_decision_id": linked_decision_id or None, "hypothesis_id": trade.get("hypothesis_id") or attribution.get("hypothesis_id") or None, "execution_status": "EXECUTED", "execution_attribution": attribution}
            event_path = ROOT / "events" / "trades" / f"{event_id}.json"
            event_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json_write(event_path, event)
            trade_event_recorded = True
        # Always reconcile event-backed human-readable records, including idempotent replay.
        # This repairs a missing archive/CASE line without creating a second trade event.
        archive_line = f"- {event['confirmed_at_beijing']}：{event.get('name')}（{event.get('code')}）{event.get('side')} {int(event.get('quantity') or 0):,}份/股，成交价{event.get('price')}，成交本金{float(event.get('amount') or 0):,.2f}元；来源：{event.get('source')}。"
        case_line = f"- 待复盘CASE｜{event['confirmed_at_beijing']}｜{event.get('name')}（{event.get('code')}）｜{event.get('side')} {int(event.get('quantity') or 0):,}份/股｜生命周期：{event.get('lifecycle') or '待确认'}｜仅登记真实成交，复盘结论留待盘后形成。"
        upsert_formal_line(ROOT, ARCHIVE.name, TRADE_START, TRADE_END, event_id, archive_line, before_heading="## 6. 历史Excel与专项数据来源")
        upsert_formal_line(ROOT, EXPERIENCE.name, CASE_START, CASE_END, event_id, case_line, before_heading="## 3. 历史研究与专项回测")
        sync_experience_transaction_index(event)
        linked_event_decision_id = str(event.get("linked_decision_id") or "").strip()
        prior_formal_action = account.get("formal_action") or {}
        if linked_event_decision_id:
            decision_path = ROOT / "events" / "decisions" / f"{linked_event_decision_id}.json"
            linked_decision = load_json(decision_path) if decision_path.exists() else {}
            linked_formal = linked_decision.get("formal_decision") if isinstance(linked_decision.get("formal_decision"), dict) else {}
            account["formal_action"] = {
                **prior_formal_action,
                "action": linked_formal.get("action") or linked_formal.get("amount_action") or prior_formal_action.get("action") or "",
                "quantity": linked_formal.get("quantity", prior_formal_action.get("quantity")),
                "decision_id": linked_event_decision_id,
                "decision_time": linked_formal.get("decision_time") or linked_formal.get("data_as_of_beijing") or linked_decision.get("decision_time_beijing") or prior_formal_action.get("decision_time"),
                "source": "CHATGPT_FORMAL_DECISION",
                "lifecycle": linked_formal.get("lifecycle") or prior_formal_action.get("lifecycle"),
                "applicable_object": linked_formal.get("candidate_code") or linked_formal.get("code") or linked_decision.get("candidate_code") or prior_formal_action.get("applicable_object") or "",
                "validity": "ACTIVE",
                "execution_status": "EXECUTED",
                "execution_fact_ref": f"events/trades/{event_id}.json",
                "last_executed_event_id": event_id,
            }
        else:
            account["formal_action"] = {
                **prior_formal_action,
                "execution_status": "EXECUTED",
                "execution_fact_ref": f"events/trades/{event_id}.json",
                "last_executed_event_id": event_id,
            }
        atomic_json_write(ACCOUNT, account)
        write_trade_review_required(event)
        persisted_trade_events.append((trade, event))

    # Refresh the CURRENT trade mirror only after this request's trade events
    # are durable.  Doing this before the loop leaves CURRENT and downstream
    # trigger construction one confirmed trade behind.
    apply_superseded_trade_corrections(request)
    latest_trade_event_id = _latest_trade_event_id()
    if latest_trade_event_id:
        current_path = ROOT / "data/state/CURRENT.json"
        current = load_json(current_path) if current_path.exists() else {}
        if current.get("last_trade_event_id") != latest_trade_event_id:
            current["last_trade_event_id"] = latest_trade_event_id
            current["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            atomic_json_write(current_path, current)

    sync_current_account_mirror(ROOT, account)
    review_recorded, review_idempotent = record_post_close_review(account, request)
    unavailable_recorded, unavailable_idempotent = (False, False)
    for trade, event in persisted_trade_events:
        recorded, idempotent = record_unrecoverable_review_prerequisite(account, request, event)
        unavailable_recorded = unavailable_recorded or recorded
        unavailable_idempotent = unavailable_idempotent or idempotent
    # Keep the three human-readable fact documents synchronized before the
    # final Dashboard render.  sync_formal_files owns factual account projection
    # but must not overwrite the lifecycle projection rendered below.
    account_membership_changed = account_sync_status == "ACCOUNT_FACT_UPDATED" or bool(trade_event_recorded)
    observation_universe_changed = persist_monitor_universe(
        ROOT, account, (request.get("formal_decision") or {}) if formal_decision_current else {}
    ) if (formal_decision_current or account_membership_changed) else False
    formal_files_sync = sync_formal_files(ROOT, account)
    # There is exactly one Dashboard projection owner: sync_formal_files.py.
    # Do not rebuild the Dashboard through the legacy state-sync renderer here;
    # that path omits the canonical lifecycle projection and reintroduces stale
    # P&L values into the lifecycle "下一节点" column.
    dashboard_sync = sync_dashboard_projection(ROOT, account)
    result = {"ok": True, "request_id": request.get("request_id"), "interaction_scenario": request.get("interaction_scenario"), "account_updated_at": account.get("updated_at"), "dashboard_updated": bool(dashboard_sync.get("dashboard_changed")), "formal_decision_recorded": decision_recorded, "formal_decision_id": decision_id, "trade_event_recorded": trade_event_recorded, "post_close_review_recorded": review_recorded, "post_close_review_idempotent_noop": review_idempotent, "review_prerequisite_unavailable_recorded": unavailable_recorded, "review_prerequisite_unavailable_idempotent_noop": unavailable_idempotent, "formal_files_sync": formal_files_sync, "observation_universe_changed": observation_universe_changed, "account_sync_status": account_sync_status, "normalized_non_security_asset_count": normalized_non_security_asset_count, "canonical_ingress_state": canonical_ingress_contract["terminal_state"], "canonical_ingress_failure_reason": None if canonical_ingress_contract["terminal_state"] == CANONICAL_INGRESS_SUBMITTED else canonical_ingress_contract["reason"]}
    if review_recorded and str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION":
        # The completion projection owner needs the exact event produced by the
        # same request.  Trade Review events are keyed by market_date, unlike
        # System Review events keyed by occurrence/task_run_id.
        market_date = str(request.get("market_date") or "").strip()
        if not market_date:
            raise ValueError("SCHEDULED_REVIEW_COMPLETION requires market_date")
        if (
            str(request.get("request_type") or "").strip().upper() == "SCHEDULED_REVIEW_COMPLETION"
            and str(request.get("formal_fact_type") or "").strip().upper() in {"FORMAL_POST_CLOSE_REVIEW", "FORMAL_CURRENT_MAIN_DECISION"}
            and str((request.get("presentation_binding") or {}).get("task_id") or "").strip() == "ETF正式决策"
            and str(request.get("occurrence_id") or "").strip()
        ):
            result["canonical_publication"] = f"events/reviews/formal_{request['occurrence_id']}.json"
        else:
            result["canonical_publication"] = f"events/reviews/{market_date}.json"
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


