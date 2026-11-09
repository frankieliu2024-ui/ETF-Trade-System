from pathlib import Path


def test_expected_session_gate_skips_record_failure_stage():
    text = (Path(__file__).resolve().parents[1] / "scripts" / "cloud_runner_snapshot.py").read_text()
    for reason in (
        "outside_a_share_capture_window",
        "morning_close_already_recorded",
        "close_already_recorded",
        "non_trading_weekend",
    ):
        marker = f'"reason": "{reason}"'
        index = text.find(marker)
        assert index >= 0
        assert '"failure_stage": "session_gate"' in text[max(0, index - 180):index + 80]
