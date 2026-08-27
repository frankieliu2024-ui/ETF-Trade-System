from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding='utf-8'))

def fail(msg: str) -> None:
    print(f'FAIL: {msg}')
    raise SystemExit(1)

current = load('data/state/CURRENT.json')
runtime = load('data/state/runtime_health.json')
snapshot_rel = str(current.get('latest_snapshot') or '')
if not snapshot_rel:
    fail('CURRENT.latest_snapshot missing')
snapshot_path = ROOT / snapshot_rel
if not snapshot_path.exists():
    fail(f'CURRENT.latest_snapshot not found: {snapshot_rel}')
snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))

for field in ('market_date', 'captured_at'):
    if str(current.get(field) or '') != str(snapshot.get(field) or ''):
        fail(f'CURRENT.{field} != snapshot.{field}')
phase = str(snapshot.get('market_phase') or '')
if str((current.get('data_freshness') or {}).get('market_phase') or '') != phase:
    fail('CURRENT market_phase != snapshot market_phase')
if str(runtime.get('latest_snapshot') or '') != snapshot_rel:
    fail('runtime_health.latest_snapshot != CURRENT.latest_snapshot')
if str(runtime.get('market_date') or '') != str(current.get('market_date') or ''):
    fail('runtime_health.market_date != CURRENT.market_date')

runtime_phase = str(runtime.get('market_phase') or '')
runtime_status = str(runtime.get('status') or '')
runtime_reason = str(runtime.get('reason') or '')
runtime_failure_stage = str(runtime.get('failure_stage') or '')
# Outside the A-share capture window, runtime_health describes the most recent
# collection attempt (SKIPPED), while CURRENT/snapshot intentionally retain the
# last valid formal close. Those are different semantics and must not be forced
# to share one market_phase. All other runtime/snapshot phase mismatches remain
# hard failures.
post_session_skip = (
    runtime_status == 'SKIPPED'
    and runtime_failure_stage == 'session_gate'
    and runtime_reason == 'outside_a_share_capture_window'
    and str(current.get('latest_valid_node') or '') == 'close'
)
if runtime_phase != phase and not post_session_skip:
    fail('runtime_health.market_phase != snapshot.market_phase')

node = str(snapshot.get('node') or '')
if node != str(current.get('latest_valid_node') or ''):
    fail('CURRENT.latest_valid_node != snapshot.node')
if phase == 'OPENING_CALL_AUCTION' and node not in {'auction', '0925'}:
    fail(f'opening-auction snapshot has non-auction node={node}')
if phase.startswith('CONTINUOUS_') and node in {'auction', '0925', 'close'}:
    fail(f'continuous snapshot has incompatible node={node}')

rows = snapshot.get('rows') or []
if len(rows) != int(snapshot.get('count') or 0):
    fail('snapshot row count mismatch')
symbols = [str(r.get('symbol') or '') for r in rows]
if len(symbols) != len(set(symbols)):
    fail('duplicate symbols in latest snapshot')
required_indices = {'000001', '000688', '399006'}
if not required_indices.issubset(set(symbols)):
    fail('missing formal A-share indices')

snap_time = datetime.fromisoformat(str(snapshot.get('captured_at')))
failed = []
for row in rows:
    status = str(row.get('quality_status') or '')
    if status in {'FAILED', 'FAIL'}:
        failed.append(str(row.get('symbol') or ''))
        continue
    asof = str(row.get('as_of_beijing') or '')
    if not asof:
        fail(f"usable row missing provider time: {row.get('symbol')}")
    dt = datetime.fromisoformat(asof)
    if (dt - snap_time).total_seconds() > 90:
        fail(f"provider time implausibly later than snapshot: {row.get('symbol')}")
    if str(row.get('market_phase') or '') != phase:
        fail(f"row market_phase mismatch: {row.get('symbol')}")

coverage = ((snapshot.get('runtime') or {}).get('analysis_coverage') or {})
if int(coverage.get('failed_count') or 0) != len(failed):
    fail('analysis_coverage.failed_count mismatch')
if sorted(coverage.get('failed_objects') or []) != sorted(failed):
    fail('analysis_coverage.failed_objects mismatch')

print(json.dumps({
    'status': 'PASS' if not failed else 'WARNING',
    'market_date': current.get('market_date'),
    'node': node,
    'market_phase': phase,
    'runtime_phase_semantics': 'POST_SESSION_SKIPPED_LAST_VALID_CLOSE_RETAINED' if post_session_skip else 'ALIGNED',
    'snapshot': snapshot_rel,
    'rows': len(rows),
    'failed_objects': failed,
}, ensure_ascii=False))
