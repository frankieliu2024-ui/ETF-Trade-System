from __future__ import annotations

import argparse
import json
from pathlib import Path

from manual_formal_decision_completion import write_completion_from_persisted_bds

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bds-path", required=True)
    parser.add_argument("--source-request", required=True)
    args = parser.parse_args()

    bds_path = str(args.bds_path).strip()
    source_path = str(args.source_request).strip()
    bds_file = (ROOT / bds_path).resolve()
    source_file = (ROOT / source_path).resolve()
    if ROOT not in bds_file.parents or ROOT not in source_file.parents:
        raise SystemExit("recovery paths must stay inside repository root")
    if not bds_file.is_file() or not source_file.is_file():
        raise SystemExit("same-parent BDS and source request are required")

    bds = json.loads(bds_file.read_text(encoding="utf-8"))
    source = json.loads(source_file.read_text(encoding="utf-8"))
    parent_id = str(source.get("request_id") or "").strip()
    bds_parent = str(bds.get("parent_request_id") or "").strip()
    bds_id = str(bds.get("request_id") or "").strip()
    if not parent_id or bds_parent != parent_id:
        raise SystemExit("BDS parent does not match the source request")
    if bds_id != f"{parent_id}__business_decision_source":
        raise SystemExit("BDS identity is not the canonical child identity")

    completion = write_completion_from_persisted_bds(
        source_path,
        bds_path,
        consumed_snapshot=str(bds.get("consumed_snapshot") or "").strip(),
        completion_request_id=f"{parent_id}__scheduled_review_completion",
    )
    print(json.dumps({
        "ok": True,
        "parent_request_id": parent_id,
        "bds_path": bds_path,
        "completion_path": str(completion.relative_to(ROOT)).replace("\\\\", "/"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
