from scripts.user_visible_presentation import _hyperliquid_placement_errors


def test_hyperliquid_metadata_belongs_to_formal_fact_boundary_not_monitoring():
    content = """【ETF正式决策】2026-11-09 09:00 正式判断

【判断时点与事实边界】
Hyperliquid读取状态为可用，采集时间为09:00，质量和覆盖范围已确认。

【市场监测】
海外市场与A股反馈存在分化。

【全市场发现】
1. 候选ETF
（1）Hyperliquid属于支持，提升买入优先级。
"""
    assert _hyperliquid_placement_errors(content) == []


def test_hyperliquid_metadata_is_rejected_from_formal_monitoring():
    content = """【ETF正式决策】2026-11-09 09:00 正式判断

【判断时点与事实边界】
本次事实边界已建立。

【市场监测】
Hyperliquid采集时间为09:00，质量状态良好，观测数量为3。

【全市场发现】
1. 候选ETF
（1）Hyperliquid属于支持，提升买入优先级。
"""
    errors = _hyperliquid_placement_errors(content)
    assert any("metadata_must_not_be_in_monitoring_section" in error for error in errors)


def test_trade_review_hyperliquid_metadata_belongs_to_time_boundary():
    content = """【ETF交易复盘】2026-11-09 20:30 完整日复盘

【复盘与数据时点（北京时间）】
Hyperliquid读取状态为可用，采集时间为20:00，质量和覆盖范围已确认。

【三层监测】
海外市场与A股反馈存在分化。

【全市场发现】
1. 候选ETF
（1）Hyperliquid属于时机过滤，维持观察。
"""
    assert _hyperliquid_placement_errors(content) == []

