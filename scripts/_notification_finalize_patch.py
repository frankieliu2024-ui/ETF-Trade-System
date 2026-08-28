from pathlib import Path


def patch_common() -> None:
    path = Path('scripts/market_notification_common.py')
    text = path.read_text(encoding='utf-8')
    marker = 'ROOT = Path(__file__).resolve().parents[1]\n'
    helper = '''\n\ndef _formal_etf_aliases() -> tuple[dict[str, str], dict[tuple[str, str], str]]:\n    """Return formal ETF names and current provider aliases for user-visible text."""\n    cfg = read_json(ROOT / "config/market/etf_monitor_universe.json", {})\n    formal = {\n        str(x.get("code") or ""): str(x.get("name") or "")\n        for x in (cfg.get("objects") or [])\n        if str(x.get("code") or "") and str(x.get("name") or "")\n    }\n    aliases: dict[tuple[str, str], str] = {}\n    current = read_json(STATE / "CURRENT.json", {})\n    snapshot_path = ROOT / str(current.get("latest_snapshot") or "")\n    snapshot = read_json(snapshot_path, {}) if snapshot_path.exists() else {}\n    for row in snapshot.get("rows") or []:\n        code = str(row.get("symbol") or "")\n        provider = str(row.get("provider_name") or "").strip()\n        if code in formal and provider and provider != formal[code]:\n            aliases[(provider, code)] = formal[code]\n    return formal, aliases\n\n\ndef _normalize_user_visible_text(value: Any) -> str:\n    text = str(value or "")\n    text = re.sub(\n        r"(\\d{4}-\\d{2}-\\d{2})T(\\d{2}:\\d{2}:\\d{2})(?:\\.\\d+)?\\+08:00",\n        r"\\1 \\2",\n        text,\n    )\n    _formal, aliases = _formal_etf_aliases()\n    for (provider, code), formal_name in aliases.items():\n        text = text.replace(f"{provider}（{code}）", f"{formal_name}（{code}）")\n    return text\n\n\ndef _normalize_user_visible_event(event: dict) -> dict:\n    event = dict(event)\n    event["title"] = _normalize_user_visible_text(event.get("title"))\n    event["content"] = _normalize_user_visible_text(event.get("content"))\n    formal, _aliases = _formal_etf_aliases()\n    code = str(event.get("security_code") or "")\n    if code in formal:\n        event["security_name"] = formal[code]\n    return event\n'''
    if '_formal_etf_aliases()' not in text:
        text = text.replace(marker, marker + helper, 1)

    old = '''def render_shock(*, what: list[str], why: str, implication: str, action: str, as_of: str, boundary: str) -> str:\n    implication, action = _decision_readable_implication(what, implication, action)\n    return (\n        "### 核心结论\\n"\n        + "\\n".join(what[:5])'''
    new = '''def render_shock(*, what: list[str], why: str, implication: str, action: str, as_of: str, boundary: str) -> str:\n    implication, action = _decision_readable_implication(what, implication, action)\n    display_what = list(what[:5])\n    if any("关键结构" in line and "当前" in line for line in display_what):\n        display_what = [line for line in display_what if "当日涨跌" not in line and "当前涨跌" not in line]\n    return (\n        "### 核心结论\\n"\n        + "\\n".join(display_what)'''
    if old not in text:
        raise SystemExit('render_shock patch anchor not found')
    text = text.replace(old, new, 1)

    old = '''def persist_and_send(event: dict, *, policy: str) -> dict:\n    event = _normalize_user_title(event)'''
    new = '''def persist_and_send(event: dict, *, policy: str) -> dict:\n    event = _normalize_user_visible_event(event)\n    event = _normalize_user_title(event)'''
    if old not in text:
        raise SystemExit('persist_and_send patch anchor not found')
    text = text.replace(old, new, 1)
    path.write_text(text, encoding='utf-8')


def patch_test_workflow() -> None:
    path = Path('.github/workflows/notification-semantics-test.yml')
    text = path.read_text(encoding='utf-8')
    start = text.index('      - name: Send A-share structure test\n')
    end = text.index('      - name: Send active ETF object test\n', start)
    new_block = '''      - name: Send A-share structure test from latest valid snapshot\n        shell: bash\n        run: |\n          PYTHONPATH=scripts python - <<'PY'\n          import json, os\n          from market_notification_common import _current_a_share_inputs, persist_and_send, render_summary\n          from notification_center import STATE, read_json\n          from notification_semantics import a_share_structure\n          indices, etfs, features = _current_a_share_inputs()\n          if not indices or not etfs:\n              raise SystemExit('latest valid A-share snapshot cannot build semantic test')\n          headline, path_lines, implication, action = a_share_structure(indices, etfs, features)\n          current = read_json(STATE / 'CURRENT.json', {})\n          as_of = str(current.get('captured_at_beijing') or current.get('captured_at') or '')\n          content = render_summary(\n              headline_lines=headline,\n              path_lines=path_lines,\n              implication=implication,\n              action=action,\n              as_of_lines=[f'- **最近有效A股行情依据（北京时间）**：{as_of}'],\n              boundary='测试可使用最近有效A股快照，不代表正式事件触发；正式阈值、MASTER、风险许可和交易动作均未改变。',\n          )\n          event = {\n              'key': f"unified-a-share-test:{os.environ.get('GITHUB_RUN_ID','manual')}:{os.environ.get('GITHUB_RUN_ATTEMPT','1')}",\n              'type': '主动通知模板测试',\n              'event_type': 'UNIFIED_A_SHARE_NOTIFICATION_TEST',\n              'title': '【测试｜A股结构】最近有效A股结构',\n              'content': '**统一通知语义测试；使用最近有效A股正式快照，不代表正式阈值触发。**\\n\\n' + content,\n              'confirmation_context': {'market': 'A_SHARE', 'a_share_as_of_beijing': as_of},\n          }\n          result = persist_and_send(event, policy='Unified A-share semantic test only; no production threshold or trading-rule change.')\n          print(json.dumps(result, ensure_ascii=False))\n          if result.get('status') != 'SENT':\n              raise SystemExit(f"expected A-share test SENT, got {result}")\n          PY\n'''
    text = text[:start] + new_block + text[end:]

    text = text.replace(
        "name = str(row.get('provider_name') or code)\n",
        "formal_cfg = read_json(Path('config/market/etf_monitor_universe.json'), {})\n          formal_names = {str(x.get('code') or ''): str(x.get('name') or '') for x in (formal_cfg.get('objects') or [])}\n          name = formal_names.get(code) or str(row.get('provider_name') or code)\n",
        1,
    )
    text = text.replace(
        "what=['- **事件类型**：对象角色语义测试', f'- **对象/结构**：{name}（{code}）', f'- **当前涨跌**：{pct(day)}', f'- **关键结构**：盘中高点相对前收{pct(high_ret)}，当前{pct(day)}'],",
        "what=['- **事件类型**：对象角色语义测试', f'- **对象/结构**：{name}（{code}）', f'- **关键结构**：盘中高点相对前收{pct(high_ret)}，当前{pct(day)}'],",
        1,
    )
    text = text.replace(
        "f\"unified-object-test:{os.environ.get('GITHUB_SHA','manual')}:{code}\"",
        "f\"unified-object-test:{os.environ.get('GITHUB_RUN_ID','manual')}:{os.environ.get('GITHUB_RUN_ATTEMPT','1')}:{code}\"",
        1,
    )
    text = text.replace(
        "raise SystemExit(1 if result.get('status') == 'CREATED' else 0)",
        "raise SystemExit(0 if result.get('status') == 'SENT' else f\"expected ETF object test SENT, got {result}\")",
        1,
    )
    text = text.replace(
        "f\"unified-us-test:{os.environ.get('GITHUB_SHA','manual')}\"",
        "f\"unified-us-test:{os.environ.get('GITHUB_RUN_ID','manual')}:{os.environ.get('GITHUB_RUN_ATTEMPT','1')}\"",
        1,
    )
    text = text.replace(
        "raise SystemExit(1 if result.get('status') == 'CREATED' else 0)",
        "raise SystemExit(0 if result.get('status') == 'SENT' else f\"expected US test SENT, got {result}\")",
        1,
    )
    path.write_text(text, encoding='utf-8')


patch_common()
patch_test_workflow()
print('notification finalization patch applied')
