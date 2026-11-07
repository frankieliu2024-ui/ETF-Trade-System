import sys

sys.path.insert(0, "scripts")
from run_guarded_notification import _pulse_test_candidates


def test_integrated_user_presentation_is_scoped_and_evidence_bearing():
    event = _pulse_test_candidates()[0]
    content = event["content"]
    assert "创新药ETF银华（159992）" in content
    assert "5日+2.5%" in content
    assert "20日+3.5%" in content
    assert "本节点涨跌+1.1%" in content
    assert "换手承接+7.4%" in content
    assert "XBI对应创新药ETF（159992）" in content
    assert "看空一侧拥挤加深" in content
    assert "潜在波动风险上升" in content
    assert "【下一步核对】" in content
    assert "【当前边界】" in content
    assert "资金费率" not in content
    assert "未平仓量" not in content
    assert "溢价" not in content
