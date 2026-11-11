"""Canonical user-visible display labels for securities."""
from __future__ import annotations
import re
from typing import Any

def security_display_name(name: Any, code: Any) -> str:
    raw_name = str(name or "").strip()
    raw_code = str(code or "").strip()
    match = re.search(r"[（(](\d{6})[）)]", raw_name)
    embedded = match.group(1) if match else ""
    if embedded:
        raw_name = re.sub(r"\s*[（(]\d{6}[）)]", "", raw_name).strip()
    code = embedded or raw_code
    if not code:
        return raw_name or "未知标的"
    return f"{raw_name}（{code}）" if raw_name else code
