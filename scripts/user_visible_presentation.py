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
    if errors:
        raise ValueError("user-visible presentation contract failed: " + "; ".join(dict.fromkeys(errors)))
