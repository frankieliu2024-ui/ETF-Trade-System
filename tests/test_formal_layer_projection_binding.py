from pathlib import Path


def test_opportunity_projection_binds_to_new_discovery_projection_timestamp():
    text = (Path(__file__).parents[1] / "scripts" / "build_query_context.py").read_text(encoding="utf-8")
    assert '"source_discovery_generated_at_beijing": projection.get("generated_at_beijing", "")' in text
    assert '"source_discovery_generated_at_beijing": discovery.get("generated_at_beijing", "")' not in text
