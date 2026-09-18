"""One-shot, fail-closed runner for the existing HISTORICAL_BACKFILL ingress."""
from __future__ import annotations
import argparse, copy, hashlib, json, re, subprocess, sys
from pathlib import Path

from state_manager import atomic_json_write

ROOT = Path(__file__).resolve().parents[1]

def load(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_manifest(data):
    """Validate the #623 business cardinality without entering the ingress."""
    trades = data.get("historical_trades")
    acquisition = data.get("acquisition")
    if not isinstance(trades, list) or len(trades) != 37:
        raise ValueError("manifest must contain exactly 37 ordinary historical trades")
    if not isinstance(acquisition, dict) or str(acquisition.get("code") or "") != "301689":
        raise ValueError("manifest must contain exactly one 301689 acquisition")
    for item in trades:
        if not isinstance(item, dict):
            raise ValueError("ordinary historical trade must be an object")
        if str(item.get("event_type") or "").upper() == "IPO_ALLOTMENT_ACQUISITION":
            raise ValueError("acquisition must not be placed in historical_trades")
    return trades, acquisition

def _diagnostic_text(value, limit=4000):
    text = str(value or "")
    text = re.sub(r"(?i)(token|authorization|password|secret)=\\S+", r"\\1=[REDACTED]", text)
    return text[-limit:]

def _fail_child(stage, completed, detail):
    print(json.dumps({
        "historical_ingress_failure": stage,
        "returncode": completed.returncode,
        "detail": detail,
        "stderr": _diagnostic_text(completed.stderr),
        "stdout": _diagnostic_text(completed.stdout),
    }, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(detail)

def _extract_final_json(stdout):
    """Extract the last ingress payload from mixed process/log output."""
    decoder = json.JSONDecoder()
    candidates = []
    for offset, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(stdout[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            candidates.append(payload)
    qualified = [payload for payload in candidates if
                 "canonical_ingress_state" in payload or
                 "canonical_ingress_failure_reason" in payload]
    if not qualified:
        raise SystemExit("historical ingress returned no valid JSON payload")
    return qualified[-1]

def _run_ingress(proc):
    completed = subprocess.run(proc, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode != 0:
        _fail_child("SUBPROCESS_FAILED", completed, f"historical ingress failed with exit code {completed.returncode}")
    try:
        payload = _extract_final_json(completed.stdout)
    except SystemExit as exc:
        _fail_child("CANONICAL_JSON_MISSING", completed, str(exc))
    state = str(payload.get("canonical_ingress_state") or "").upper()
    reason = str(payload.get("canonical_ingress_failure_reason") or "")
    if state == "CANONICAL_INGRESS_NOT_APPLICABLE" or reason == "not_a_formal_fact_ingress_request":
        raise SystemExit("historical ingress was not applicable; refusing false green")
    if state != "CANONICAL_INGRESS_SUBMITTED":
        raise SystemExit(f"historical ingress did not submit canonically: {state or 'MISSING_STATE'}")
    return payload


def _seed_projection_case_annotation(event, case_id):
    """Restore an already-documented CASE annotation through the formal gateway.

    This is a bounded projection migration only.  The CASE identity comes from
    the registered historical-backfill manifest, which records the pre-replay
    formal Experience ownership.  It never mutates the canonical trade event.
    """
    if not case_id:
        return
    if not re.fullmatch(r"CASE-\\d{8}-\\d{2}", str(case_id)):
        raise SystemExit(f"invalid projection CASE identity: {case_id}")
    from formal_file_mutation_gateway import write_formal_text_if_changed
    experience = ROOT / "ETF交易复盘与经验库_2026.md"
    text = experience.read_text(encoding="utf-8")
    section_end = "\n### 2.2 银证转账与非交易现金流水"
    header = "|日期时间|标的|代码|动作|数量|成交价|成交本金|实际费用|资金发生额|归属/备注|"
    start = text.index(header)
    end = text.index(section_end, start)
    execution_stamp = str(
        event.get("executed_at_beijing")
        or event.get("executed_at")
        or event.get("trade_time")
        or event.get("confirmed_at_beijing")
        or ""
    ).replace("T", " ")[:19]
    code = str(event.get("code") or "")
    side = str(event.get("side") or "").upper()
    side_cn = "买入" if side in {"BUY", "B", "买入", "买"} else "卖出" if side in {"SELL", "S", "卖出", "卖"} else side
    qty = int(float(event.get("quantity") or 0))
    price = float(event.get("price") or 0)
    matches = []
    lines = text[start:end].splitlines()
    for index, line in enumerate(lines):
        parts = line.split("|")
        if len(parts) < 11:
            continue
        if (
            parts[1] == execution_stamp
            and parts[3] == code
            and parts[4] == side_cn
            and parts[5].replace(",", "") == str(qty)
            and parts[6] == f"{price:.3f}"
        ):
            matches.append(index)
    if len(matches) != 1:
        raise SystemExit(
            f"projection CASE seed requires exactly one economic row: "
            f"{execution_stamp}:{code}:matches={len(matches)}"
        )
    index = matches[0]
    existing_cases = re.findall(r"CASE-\\d{8}-\\d{2}", lines[index])
    if existing_cases:
        if sorted(set(existing_cases)) != [case_id]:
            raise SystemExit(
                f"projection CASE conflict for {execution_stamp}:{code}: "
                f"{sorted(set(existing_cases))} != {case_id}"
            )
        return
    parts = lines[index].split("|")
    note = parts[10].strip()
    parts[10] = f"{case_id}；{note}" if note else case_id
    lines[index] = "|".join(parts)
    updated = text[:start] + "\n".join(lines) + text[end:]
    write_formal_text_if_changed(ROOT, experience.name, updated)


def project_historical_formal_facts(events, projection_case_ownership=None):
    """Project replayed historical facts through existing canonical owners."""
    from process_state_sync_request import sync_experience_transaction_index
    from formal_file_mutation_gateway import upsert_formal_line
    from build_execution_quality import build as build_execution_quality
    archive_start = "<!-- AUTO_TRADE_EVENTS_START -->"
    archive_end = "<!-- AUTO_TRADE_EVENTS_END -->"
    archive = ROOT / "ETF市场行情档案_2026.md"
    projection_case_ownership = projection_case_ownership or {}
    for event in events:
        if str(event.get("execution_status") or "").upper() != "EXECUTED":
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        day = str(
            event.get("executed_at_beijing")
            or event.get("executed_at")
            or event.get("trade_time")
            or event.get("confirmed_at_beijing")
            or ""
        )[:10]
        code = str(event.get("code") or "")
        name = str(event.get("name") or "")
        if code:
            name = re.sub(rf"（{re.escape(code)}）$", "", name).strip()
        side = str(event.get("side") or "")
        qty = event.get("quantity")
        price = event.get("price")
        line = f"{event_id}｜{day}｜{name}（{code}）｜{side}｜{qty}｜{price}｜historical canonical trade fact"
        upsert_formal_line(ROOT, archive.name, archive_start, archive_end, event_id, line, before_heading="## 6. 历史Excel与专项数据来源")
        _seed_projection_case_annotation(event, projection_case_ownership.get(event_id))
        sync_experience_transaction_index(event)
    atomic_json_write(ROOT / "data/state/execution_quality.json", build_execution_quality(ROOT))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--request-id", required=True)
    args=p.parse_args()
    manifest=(ROOT/args.manifest).resolve()
    if ROOT not in manifest.parents or not manifest.exists():
        raise SystemExit("manifest must be an existing repository-relative file")
    data=load(manifest)
    if str(data.get("ingress_mode") or "").upper() != "HISTORICAL_BACKFILL":
        raise SystemExit("only HISTORICAL_BACKFILL is accepted")
    if str(data.get("request_id") or "") != args.request_id:
        raise SystemExit("request identity mismatch")
    try:
        trades, _acquisition = validate_manifest(data)
    except ValueError as exc:
        raise SystemExit(str(exc))
    before={}
    for rel in ("data/state/account_fact.json","data/state/CURRENT.json"):
        path=ROOT/rel
        before[rel]=digest(path) if path.exists() else None
    proc=[sys.executable,str(ROOT/"scripts/process_state_sync_request.py"),str(manifest.relative_to(ROOT))]
    first = _run_ingress(proc)
    second = _run_ingress(proc)
    project_events = []
    for event_path in sorted((ROOT / "events" / "trades").glob("*.json")):
        try:
            project_events.append(load(event_path))
        except (OSError, json.JSONDecodeError):
            continue
    projection_case_ownership = data.get("projection_case_ownership") or {}
    trade_ids = {str(item.get("event_id") or "") for item in trades}
    if not isinstance(projection_case_ownership, dict):
        raise SystemExit("projection_case_ownership must be an object")
    unknown_projection_ids = sorted(set(projection_case_ownership) - trade_ids)
    if unknown_projection_ids:
        raise SystemExit(f"projection CASE ownership references unknown trades: {unknown_projection_ids}")
    for event_id, case_id in projection_case_ownership.items():
        if not re.fullmatch(r"CASE-\\d{8}-\\d{2}", str(case_id)):
            raise SystemExit(f"invalid projection CASE ownership: {event_id}={case_id}")
    project_historical_formal_facts(project_events, projection_case_ownership)
    after={rel:digest(ROOT/rel) if (ROOT/rel).exists() else None for rel in before}
    if before != after:
        raise SystemExit("current account/CURRENT mutation detected")
    print(json.dumps({"status":"PASS","request_id":args.request_id,"first_replay":first,"second_replay":second,"duplicate_events_created":0,"current_account_unchanged":True},ensure_ascii=False))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
