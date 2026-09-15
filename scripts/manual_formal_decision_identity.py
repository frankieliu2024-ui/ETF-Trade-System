from __future__ import annotations

import hashlib
import json


def canonical_manual_identity(request: dict, formal_decision: dict, market_date: str) -> tuple[str, str, str]:
    """Return request, parent, and fingerprint for formal-decision persistence.

    Manual Chat completions use the originating manual request as canonical
    identity across completion-envelope retries. Other paths retain the legacy
    request identity.
    """
    request_id = str(request.get("request_id") or "").strip()
    parent_request_id = str(request.get("parent_request_id") or "").strip()
    is_manual_completion = str(request.get("source") or "").strip() == "CHATGPT_MANUAL_FORMAL_COMPLETION"
    canonical_request_id = parent_request_id if is_manual_completion and parent_request_id else request_id
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "request_id": canonical_request_id,
                "market_date": market_date,
                "formal_decision": formal_decision,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return request_id, parent_request_id, fingerprint
