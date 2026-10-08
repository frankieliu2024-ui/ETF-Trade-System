from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CLOSURE_PHRASES = ("本事项已闭环", "本事项结束，不再追加维护")
INTERNAL_MARKERS = (
    "request_id", "request id", "workflow run", "run_id", "run id",
    "provider log", "commit sha", "head sha",
)

# High-confidence control-plane vocabulary that has a plain business-language
# equivalent. Keep this list deliberately bounded: it protects presentation
# without becoming a general English/NLP blacklist or changing machine schemas.
CONTROL_PLANE_PATTERNS = (
    (r"(?i)(?<![\w-])request-bound(?![\w-])", "request-bound"),
    (r"(?<![A-Z0-9_])READY(?![A-Z0-9_])", "READY"),
    (r"(?i)(?<![\w-])blocker(?:s)?(?![\w-])", "blocker"),
    (r"(?<![A-Z0-9_])NO_ADD(?![A-Z0-9_])", "NO_ADD"),
    (r"(?<![A-Z0-9_])BUSINESS_DECISION_READY(?![A-Z0-9_])", "BUSINESS_DECISION_READY"),
    (r"(?i)(?<![\w-])reply_freezable(?![\w-])", "reply_freezable"),
    (r"(?i)(?<![\w-])canonical\s+persistence(?![\w-])", "canonical persistence"),
    (r"(?i)(?<![\w-])canonical(?![\w-])", "canonical"),
    (r"(?i)(?<![\w-])Discovery(?![\w-])", "Discovery"),
    (r"(?i)(?<![\w-])Observation\s+(?:ADMIT|RETAIN|EXIT)(?![\w-])", "Observation state enum"),
    (r"(?<![A-Z0-9_])(?:ADMIT|RETAIN|EXIT)(?![A-Z0-9_])", "ADMIT/RETAIN/EXIT"),
)


def canonical_security_map(root: Path | None = None) -> dict[str, str]:
    """Build a read-only display map from existing canonical/config facts."""
    root = (root or ROOT).resolve()
    mapping: dict[str, str] = {}
    sources = (
        root / "config/market/etf_monitor_universe.json",
        root / "data/state/account_fact.json",
        root / "data/state/asset_roles.json",
    )
    for path in sources:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "etf_monitor_universe.json":
            rows = data.get("objects") or []
        elif path.name == "account_fact.json":
            rows = data.get("positions") or []
        else:
            rows = [
                {"code": code, "name": body.get("name")}
                for code, body in (data.get("roles") or {}).items()
                if isinstance(body, dict)
            ]
        for row in rows:
            code = str((row or {}).get("code") or "").strip()
            name = str((row or {}).get("name") or "").strip()
            if code and name:
                mapping[code] = name
    return mapping


def _security_identity_errors(content: str, security_map: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for code, name in security_map.items():
        for match in re.finditer(rf"(?<!\d){re.escape(code)}(?!\d)", content):
            start, end = match.span()
            expected = f"{name}（{code}）"
            left = max(0, end - len(expected))
            if content[left:end] == expected:
                continue
            errors.append(f"known security must use canonical display identity: {expected}")
            break
    return errors


def validate_user_visible_content(
    content: str,
    *,
    canonical_closure_confirmed: bool = False,
    technical_audit_mode: bool = False,
    security_map: dict[str, str] | None = None,
    root: Path | None = None,
) -> None:
    """Fail closed on user-visible presentation drift; never rewrite prose."""
    text = str(content or "").strip()
    if not text:
        raise ValueError("user-visible content must not be empty")
    mapping = security_map if security_map is not None else canonical_security_map(root)
    errors = _security_identity_errors(text, mapping)
    if not canonical_closure_confirmed:
        for phrase in CLOSURE_PHRASES:
            if phrase in text:
                errors.append(f"closure phrase requires canonical persistence/readback acceptance: {phrase}")
    if not technical_audit_mode:
        lower = text.lower()
        for marker in INTERNAL_MARKERS:
            if marker in lower:
                errors.append(f"internal identity is forbidden in normal business presentation: {marker}")
        for pattern, label in CONTROL_PLANE_PATTERNS:
            if re.search(pattern, text):
                errors.append(
                    f"control-plane vocabulary must be translated into business language: {label}"
                )
    if errors:
        raise ValueError("user-visible presentation contract failed: " + "; ".join(dict.fromkeys(errors)))


def validate_formal_decision_reply_nonblocking(
    content: str,
    *,
    canonical_closure_confirmed: bool = False,
    technical_audit_mode: bool = False,
    security_map: dict[str, str] | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    """Validate presentation without revoking an already-earned BUSINESS_DECISION_READY reply.

    This helper is deliberately side-effect free and performs no network, persistence,
    projection, notification, or canonical writes. Presentation defects are returned
    for caller-visible correction/audit; they are not a new business-decision gate.
    """
    try:
        validate_user_visible_content(
            content,
            canonical_closure_confirmed=canonical_closure_confirmed,
            technical_audit_mode=technical_audit_mode,
            security_map=security_map,
            root=root,
        )
    except ValueError as exc:
        return {
            "business_reply_eligible": True,
            "presentation_valid": False,
            "presentation_error": str(exc),
        }
    return {
        "business_reply_eligible": True,
        "presentation_valid": True,
        "presentation_error": "",
    }



def build_formal_decision_presentation_binding(
    content: str,
    *,
    request_id: str,
    decision_id: str,
    canonical_closure_confirmed: bool = False,
    technical_audit_mode: bool = False,
    security_map: dict[str, str] | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    """Return a deterministic binding for the exact Formal Decision reply text.

    The business decision remains independently eligible; this binding only
    records whether the proposed user-visible text satisfies the presentation
    contract and fingerprints the exact text that was checked.
    """
    safe_request_id = str(request_id or "").strip()
    safe_decision_id = str(decision_id or "").strip()
    if not safe_request_id or not safe_decision_id:
        raise ValueError("formal decision presentation binding requires request_id and decision_id")
    result = validate_formal_decision_reply_nonblocking(
        content,
        canonical_closure_confirmed=canonical_closure_confirmed,
        technical_audit_mode=technical_audit_mode,
        security_map=security_map,
        root=root,
    )
    import hashlib
    text = str(content or "").strip()
    result.update({
        "schema_version": "formal-decision-presentation-binding-v1",
        "request_id": safe_request_id,
        "decision_id": safe_decision_id,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "technical_audit_mode": bool(technical_audit_mode),
    })
    return result
