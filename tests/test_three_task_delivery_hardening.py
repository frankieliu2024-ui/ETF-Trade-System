from pathlib import Path


OWNER = Path("scripts/scheduled_review_completion_owner.py").read_text(encoding="utf-8")
WORKFLOW = Path(".github/workflows/decision-notification.yml").read_text(encoding="utf-8")
INDEX = Path("ETF_SYSTEM_INDEX.md").read_text(encoding="utf-8")


def test_recovery_reuses_same_completion_and_explicitly_forces_resend():
    assert "def dispatch_notification(completion_path: str, *, force_resend: bool = False)" in OWNER
    assert 'inputs["force_resend"] = "true"' in OWNER
    assert "dispatch_notification(completion_path, force_resend=True)" in OWNER
    assert "same_completion_path" in OWNER
    assert "recovery_limit" in OWNER


def test_three_tasks_use_one_completion_owner_and_one_notification_workflow():
    assert "scheduled_review_completion_path" in WORKFLOW
    assert "ETF_FORMAL_DECISION" in WORKFLOW
    assert "ETF_TRADE_REVIEW" in WORKFLOW
    assert "ETF_SYSTEM_REVIEW" in WORKFLOW
    assert "decision-notification.yml" in INDEX
    assert "不经过KV、HTTP ingress、consumer或独立completion owner" in INDEX


def test_delivery_states_are_not_collapsed_into_user_receipt():
    assert "completion写入、completion回读、正式事件、REPORT、notification、服务商投递和用户收到必须分别核验" in INDEX
    assert "不得把completion写入、事件形成、通知workflow提交、服务商投递或历史通知表述为用户已收到" in INDEX
