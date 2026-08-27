from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
# Trigger-only revision after the temporary workflow exists on the branch.


def require_replace(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


def patch_process() -> None:
    path = ROOT / "scripts/process_state_sync_request.py"
    text = path.read_text(encoding="utf-8")
    text = require_replace(
        text,
        "from state_manager import atomic_json_write\nfrom sync_formal_files import sync_formal_files\n",
        "from state_manager import atomic_json_write\nfrom sync_formal_files import sync_formal_files\nfrom formal_file_mutation_gateway import (\n    append_managed_line,\n    replace_managed_block as replace_block,\n    upsert_formal_line,\n    upsert_managed_line,\n    write_formal_text_if_changed,\n)\n",
        "process imports",
    )
    pattern = re.compile(r"\ndef replace_block\(.*?(?=\ndef money\()", re.S)
    text, count = pattern.subn("\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"process helper block removal count={count}")
    text = require_replace(
        text,
        '        ARCHIVE.write_text(upsert_managed_line(ARCHIVE.read_text(encoding="utf-8"), REVIEW_ARCHIVE_START, REVIEW_ARCHIVE_END, market_date, archive_entry), encoding="utf-8")',
        '        upsert_formal_line(ROOT, ARCHIVE.name, REVIEW_ARCHIVE_START, REVIEW_ARCHIVE_END, market_date, archive_entry)',
        "post-close archive",
    )
    text = require_replace(
        text,
        '        EXPERIENCE.write_text(upsert_managed_line(EXPERIENCE.read_text(encoding="utf-8"), REVIEW_EXPERIENCE_START, REVIEW_EXPERIENCE_END, market_date, experience_entry), encoding="utf-8")',
        '        upsert_formal_line(ROOT, EXPERIENCE.name, REVIEW_EXPERIENCE_START, REVIEW_EXPERIENCE_END, market_date, experience_entry)',
        "post-close experience",
    )
    text = require_replace(
        text,
        '    EXPERIENCE.write_text(text, encoding="utf-8")',
        '    write_formal_text_if_changed(ROOT, EXPERIENCE.name, text)',
        "transaction index write",
    )
    text = require_replace(
        text,
        '    DASHBOARD.write_text(dashboard, encoding="utf-8")',
        '    write_formal_text_if_changed(ROOT, DASHBOARD.name, dashboard)',
        "dashboard write",
    )
    text = require_replace(
        text,
        '        ARCHIVE.write_text(upsert_managed_line(ARCHIVE.read_text(encoding="utf-8"), TRADE_START, TRADE_END, event_id, archive_line), encoding="utf-8")',
        '        upsert_formal_line(ROOT, ARCHIVE.name, TRADE_START, TRADE_END, event_id, archive_line)',
        "trade archive write",
    )
    text = require_replace(
        text,
        '        EXPERIENCE.write_text(upsert_managed_line(EXPERIENCE.read_text(encoding="utf-8"), CASE_START, CASE_END, event_id, case_line), encoding="utf-8")',
        '        upsert_formal_line(ROOT, EXPERIENCE.name, CASE_START, CASE_END, event_id, case_line)',
        "trade experience write",
    )
    path.write_text(text, encoding="utf-8")


def patch_sync() -> None:
    path = ROOT / "scripts/sync_formal_files.py"
    text = path.read_text(encoding="utf-8")
    text = require_replace(
        text,
        "from pathlib import Path\n",
        "from pathlib import Path\n\nfrom formal_file_mutation_gateway import (\n    replace_managed_block as replace_block,\n    write_formal_text_if_changed,\n)\n",
        "sync imports",
    )
    pattern = re.compile(r"\ndef replace_block\(.*?(?=\ndef display_name\()", re.S)
    text, count = pattern.subn("\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"sync helper block removal count={count}")
    text = require_replace(
        text,
        '        dash_path.write_text(new_dash, encoding="utf-8")',
        '        write_formal_text_if_changed(root, dash_path.name, new_dash)',
        "sync dashboard write",
    )
    text = require_replace(
        text,
        '        archive_path.write_text(new_archive, encoding="utf-8")',
        '        write_formal_text_if_changed(root, archive_path.name, new_archive)',
        "sync archive write",
    )
    text = require_replace(
        text,
        '        experience_path.write_text(new_experience, encoding="utf-8")',
        '        write_formal_text_if_changed(root, experience_path.name, new_experience)',
        "sync experience write",
    )
    path.write_text(text, encoding="utf-8")


def patch_correction() -> None:
    path = ROOT / "scripts/apply_trade_fact_correction.py"
    text = path.read_text(encoding="utf-8")
    text = require_replace(
        text,
        "from typing import Any\n",
        "from typing import Any\n\nfrom formal_file_mutation_gateway import upsert_formal_line\n",
        "correction import",
    )
    pattern = re.compile(r"\ndef upsert_line\(.*?(?=\ndef pending_fee_count\()", re.S)
    text, count = pattern.subn("\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"correction helper removal count={count}")
    text = require_replace(
        text,
        '    upsert_line(ARCHIVE, ARCHIVE_START, ARCHIVE_END, correction_key, f"- {line}")',
        '    upsert_formal_line(ROOT, ARCHIVE.name, ARCHIVE_START, ARCHIVE_END, correction_key, f"- {line}")',
        "correction archive",
    )
    text = require_replace(
        text,
        '    upsert_line(EXPERIENCE, EXPERIENCE_START, EXPERIENCE_END, correction_key, f"- 事实补充｜{line} 不新增CASE、不改变历史交易判断。")',
        '    upsert_formal_line(ROOT, EXPERIENCE.name, EXPERIENCE_START, EXPERIENCE_END, correction_key, f"- 事实补充｜{line} 不新增CASE、不改变历史交易判断。")',
        "correction experience",
    )
    text = require_replace(
        text,
        '    upsert_line(DASHBOARD, DASH_START, DASH_END, correction_key, f"- {dashboard_line}")',
        '    upsert_formal_line(ROOT, DASHBOARD.name, DASH_START, DASH_END, correction_key, f"- {dashboard_line}")',
        "correction dashboard",
    )
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_process()
    patch_sync()
    patch_correction()
    print("formal gateway source transformation applied")
