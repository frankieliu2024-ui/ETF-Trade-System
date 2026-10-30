"""User-facing clarity guard applies only to System Review frozen reports."""
import pytest
from scripts.scheduled_review_completion import validate_system_review_user_facing_clarity


@pytest.mark.parametrize("content", [
    "本次运行 request_id=internal-123，已完成。",
    "平台未提供 task_run_id: unknown。",
    "内部状态 CURRENT.close_review_closure_is_stale。",
    "入口为 MANUAL_CONVERSATION。",
])
def test_system_review_rejects_internal_identifiers(content):
    with pytest.raises(ValueError, match="internal audit identifiers"):
        validate_system_review_user_facing_clarity(content)


@pytest.mark.parametrize("content", [
    "【晚间复核结论】系统一致性检查通过，暂无硬错误。",
    "【未闭环事项】收盘复核记录尚未同步到最新交易日，自动恢复受限。",
    "【通知与记录状态】本次复核记录已保存并核实，PushPlus通知已投递。",
    "【关键事实】ETF持仓和Discovery观察层身份保持独立。",
])
def test_system_review_allows_readable_operational_facts(content):
    validate_system_review_user_facing_clarity(content)
