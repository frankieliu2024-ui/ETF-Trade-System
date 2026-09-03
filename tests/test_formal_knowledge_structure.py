import json
from pathlib import Path

from scripts.formal_document_structure import validate_files


def test_formal_knowledge_sections_use_semantic_owners(tmp_path: Path):
    experience = """# E
## 0. 文件导航
## 1. 当前有效经验
## 2. 真实交易CASE
### 2.1 交易索引
### 2.2 现金
### 2.3 CASE-20260713-01：历史
<!-- AUTO_CASE_INTAKE_START -->
trade｜- 已归入CASE-20260713-01
<!-- AUTO_CASE_INTAKE_END -->
<!-- AUTO_CASE_DETAILS_START -->
### 2.16 CASE-20260902-01：新CASE
<!-- AUTO_CASE_DETAILS_END -->
## 3. 历史研究与专项回测
## 4. OBS观察
### 4.3 正式日度复盘与 CASE 延续
<!-- AUTO_POST_CLOSE_REVIEW_CASES_START -->
review
<!-- AUTO_POST_CLOSE_REVIEW_CASES_END -->
## 5. 研究与经验转化
## 6. 版本维护记录
### 6.1 正式复盘前置条件不可用记录
<!-- AUTO_REVIEW_PREREQUISITE_UNAVAILABLE_START -->
unavailable
<!-- AUTO_REVIEW_PREREQUISITE_UNAVAILABLE_END -->
"""
    archive = """# A
## 5. 历史行情、成交与账户快照
<!-- AUTO_POST_CLOSE_REVIEW_FACTS_START -->
x
<!-- AUTO_POST_CLOSE_REVIEW_FACTS_END -->
<!-- AUTO_TRADE_EVENTS_START -->
x
<!-- AUTO_TRADE_EVENTS_END -->
<!-- AUTO_ACCOUNT_FACT_SYNC_START -->
x
<!-- AUTO_ACCOUNT_FACT_SYNC_END -->
## 6. 历史Excel与专项数据来源
## 7. 数据维护规则
<!-- AUTO_TRADE_FACT_CORRECTIONS_START -->
x
<!-- AUTO_TRADE_FACT_CORRECTIONS_END -->
"""
    dashboard = """# D
<!-- AUTO_STATE_SYNC_START -->
159326
<!-- AUTO_STATE_SYNC_END -->
|ETF层当前结构|当前持仓与观察角色仅以上方canonical account projection为准|
"""
    (tmp_path / "ETF交易复盘与经验库_2026.md").write_text(experience, encoding="utf-8")
    (tmp_path / "ETF市场行情档案_2026.md").write_text(archive, encoding="utf-8")
    (tmp_path / "ETF当前状态_DASHBOARD.md").write_text(dashboard, encoding="utf-8")
    (tmp_path / "data/state").mkdir(parents=True)
    (tmp_path / "data/state/account_fact.json").write_text(json.dumps({"positions":[{"asset_type":"ETF","code":"159326"}]}), encoding="utf-8")
    assert validate_files(tmp_path) == []


def test_manual_contribution_index_is_rejected(tmp_path: Path):
    test_formal_knowledge_sections_use_semantic_owners(tmp_path)
    path = tmp_path / "ETF交易复盘与经验库_2026.md"
    path.write_text(path.read_text(encoding="utf-8").replace("### 2.16 CASE目录与映射", "### 2.16 CASE系统贡献索引"), encoding="utf-8")
    assert "manual_case_contribution_index_present" in validate_files(tmp_path)


def test_case_sequence_has_no_technical_navigation_headings():
    text = Path("ETF交易复盘与经验库_2026.md").read_text(encoding="utf-8")
    assert "### 2.16 CASE目录与映射" not in text
    assert "### 2.17 CASE详细记录" not in text
    assert "### 2.16 CASE-20260902-01：" in text
