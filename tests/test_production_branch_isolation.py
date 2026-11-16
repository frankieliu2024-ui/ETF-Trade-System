from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_market_snapshot_publishers_are_main_branch_only():
    source = (ROOT / ".github/workflows/market-snapshot.yml").read_text(encoding="utf-8")
    for marker in (
        "Canonicalize legacy formal request ingress",
        "Apply confirmed trade fact corrections",
        "Publish core snapshot and CURRENT immediately",
        "Project Formal Decision completion through market snapshot owner",
        "Record broker account sync maintenance receipt",
        "Commit downstream runtime/data artifacts",
    ):
        block = source[source.index("- name: " + marker):]
        condition = block[block.index("if: "):block.index("\n", block.index("if: "))]
        assert "github.ref == 'refs/heads/main'" in condition, marker


def test_other_runtime_publishers_are_main_branch_only():
    fallback = (ROOT / ".github/workflows/opening-auction-current-fallback.yml").read_text(encoding="utf-8")
    watchdog = (ROOT / ".github/workflows/self-healing-watchdog.yml").read_text(encoding="utf-8")
    assert "github.ref == 'refs/heads/main'" in fallback[fallback.index("Publish repaired CURRENT and contexts"):]
    assert "github.ref == 'refs/heads/main'" in watchdog[watchdog.index("Persist bounded self-healing state and deterministic repairs"):]
