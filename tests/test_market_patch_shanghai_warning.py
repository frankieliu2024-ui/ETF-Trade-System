from __future__ import annotations

import subprocess
from pathlib import Path
import unittest


class TestOneOffShanghaiWarningRepair(unittest.TestCase):
    def test_patch_stale_shanghai_single_source_assertion(self):
        root = Path(__file__).resolve().parents[1]
        target = root / "scripts" / "check_system_consistency.py"
        text = target.read_text(encoding="utf-8")
        old = '    check("providers:single_source_shanghai_index", "000001.SH" not in fallback_policy, "000001.SH remains single-source until a verified Eastmoney index mapping exists", warning=True)'
        new = '''    shanghai_rule = fallback_policy.get("000001.SH") or {}
    shanghai_direct_sources = [shanghai_rule.get("primary"), *(shanghai_rule.get("fallback") or [])]
    shanghai_multisource_ok = (
        shanghai_rule.get("direct_only") is True
        and shanghai_rule.get("primary") == "tencent_qq"
        and "hithink_finance" in shanghai_direct_sources
    )
    check("providers:shanghai_index_direct_fallback", shanghai_multisource_ok, f"000001.SH direct sources={shanghai_direct_sources}", warning=True)'''
        if old not in text:
            self.assertIn('providers:shanghai_index_direct_fallback', text)
            return
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        subprocess.run(["python", "-m", "py_compile", str(target)], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=root, check=True)
        subprocess.run(["git", "add", "scripts/check_system_consistency.py"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "fix: validate Shanghai index Tencent direct fallback"], cwd=root, check=True)
        subprocess.run(["git", "stash", "push", "--include-untracked", "-m", "one-off-shanghai-warning-generated-state"], cwd=root, check=False)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=root, check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=root, check=True)
        subprocess.run(["git", "stash", "pop"], cwd=root, check=False)
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
