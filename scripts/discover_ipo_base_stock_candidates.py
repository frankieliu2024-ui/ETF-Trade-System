from __future__ import annotations

"""Bounded full-market candidate supply for IPO base-stock replacement research.

This module is research-only.  It discovers same-market A-share identities and
cheap cross-sectional evidence that may justify heavier base-stock research.
It never grants Observation, Trial, Confirm, amount, or buy authority.
"""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
SPOT_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"
FIELDS = "f2,f3,f6,f7,f8,f10,f12,f13,f14,f15,f16,f17,f18,f24,f25,f26,f124"
MARKET_FS = {
    "SH": "m:1+t:2,m:1+t:23",
    "SZ": "m:0+t:6,m:0+t:80",
}
# Resource protection only: this is not a score/ranking quota.  Candidates are
# selected by evidence-diversity buckets and deterministic within-bucket order.
MAX_RESEARCH_CANDIDATES = 12


def _num(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def is_a_share_code(code: str, market: str) -> bool:
    code = str(code)
    return (
        len(code) == 6
        and code.isdigit()
        and ((market == "SH" and code.startswith(("60", "68")))
             or (market == "SZ" and code.startswith(("00", "30"))))
    )


def _request_json(params: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    req = Request(
        f"{SPOT_URL}?{urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_market_cross_section(market: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if market not in MARKET_FS:
        raise ValueError(f"unsupported market: {market}")
    rows: list[dict[str, Any]] = []
    page = 1
    total: int | None = None
    while True:
        payload = _request_json({
            "pn": page, "pz": 100, "po": 0, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2, "invt": 2,
            "fid": "f12", "fs": MARKET_FS[market], "fields": FIELDS,
        })
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        observed_total = int(data.get("total") or 0)
        if total is None:
            total = observed_total
        elif observed_total and total and observed_total != total:
            raise RuntimeError(f"A-share pagination total changed: first={total} page{page}={observed_total}")
        for raw in diff:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("f12") or "")
            if not is_a_share_code(code, market):
                continue
            rows.append({
                "code": code,
                "name": str(raw.get("f14") or code),
                "market": market,
                "price": _num(raw.get("f2")),
                "change_pct": _num(raw.get("f3")),
                "amount": _num(raw.get("f6")),
                "amplitude_pct": _num(raw.get("f7")),
                "turnover_pct": _num(raw.get("f8")),
                "volume_ratio": _num(raw.get("f10")),
                "return_60d_pct": _num(raw.get("f24")),
                "return_ytd_pct": _num(raw.get("f25")),
                "listing_date": str(raw.get("f26") or ""),
                "provider_timestamp": raw.get("f124"),
            })
        if not diff or not total or page * 100 >= total:
            break
        page += 1
        if page > 80:
            raise RuntimeError("A-share pagination exceeded safety bound")
    unique = {x["code"]: x for x in rows}
    return [unique[k] for k in sorted(unique)], {
        "provider": "eastmoney_push2delay",
        "market": market,
        "reported_total": total or 0,
        "parsed_a_share_count": len(unique),
        "page_count": page,
    }


def cheap_eligible(row: dict[str, Any], held_codes: set[str]) -> bool:
    """Reject only objects that cannot support bounded research.

    No return/volatility threshold is used.  Price and turnover merely establish
    that the security has a usable current cross-section; heavier research owns
    the investment judgment.
    """
    if row["code"] in held_codes:
        return False
    if row.get("price") is None or row.get("price") <= 0:
        return False
    if row.get("amount") is None or row.get("amount") <= 0:
        return False
    name = str(row.get("name") or "").upper()
    if "ST" in name or "退" in name:
        return False
    return True


def _bucket(row: dict[str, Any]) -> str:
    """Evidence-diversity bucket, not an investment score."""
    amount = row.get("amount") or 0
    amp = row.get("amplitude_pct")
    r60 = row.get("return_60d_pct")
    if amount >= 1_000_000_000:
        return "HIGH_LIQUIDITY"
    if amp is not None and amp <= 2.0:
        return "LOW_INTRADAY_VARIATION"
    if r60 is not None and abs(r60) <= 10.0:
        return "MIDDLE_60D_PATH"
    return "OTHER_EXECUTABLE"


def bounded_candidates(rows: list[dict[str, Any]], held_codes: set[str], limit: int = MAX_RESEARCH_CANDIDATES) -> list[dict[str, Any]]:
    eligible = [dict(x, evidence_bucket=_bucket(x)) for x in rows if cheap_eligible(x, held_codes)]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        groups.setdefault(row["evidence_bucket"], []).append(row)
    # Within each evidence class, deterministic ordering uses liquidity only as
    # a research-cost/executability preference.  No composite investment score.
    for group in groups.values():
        group.sort(key=lambda x: (-(x.get("amount") or 0), x["code"]))
    order = ["HIGH_LIQUIDITY", "LOW_INTRADAY_VARIATION", "MIDDLE_60D_PATH", "OTHER_EXECUTABLE"]
    out: list[dict[str, Any]] = []
    while len(out) < limit:
        progressed = False
        for key in order:
            group = groups.get(key) or []
            if group and len(out) < limit:
                row = group.pop(0)
                row["authority"] = "RESEARCH_CANDIDATE_ONLY"
                out.append(row)
                progressed = True
        if not progressed:
            break
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_file")
    args = parser.parse_args()
    req_path = ROOT / args.request_file
    req = json.loads(req_path.read_text(encoding="utf-8"))
    market = str(req["market"]).upper()
    request_id = str(req["request_id"])
    held_codes = {str(x) for x in (req.get("held_codes") or [])}
    rows, source_meta = fetch_market_cross_section(market)
    candidates = bounded_candidates(rows, held_codes, int(req.get("max_candidates") or MAX_RESEARCH_CANDIDATES))
    out = {
        "ok": True,
        "request_id": request_id,
        "processed_at": datetime.now(SHANGHAI).isoformat(),
        "market": market,
        "authority": "RESEARCH_CANDIDATE_ONLY",
        "purpose": "IPO_BASE_STOCK_REPLACEMENT_RESEARCH_SUPPLY",
        "source": source_meta,
        "universe_count": len(rows),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "guardrail": "candidate discovery is research-only; no Observation/Trial/Confirm/buy authority",
    }
    out_dir = ROOT / "research" / "base_stock_discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{request_id}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "request_id": request_id, "count": len(candidates)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
