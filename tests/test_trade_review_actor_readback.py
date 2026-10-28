from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import scheduled_review_completion_owner as owner


class TradeReviewCompletionOwnerReadbackTests(unittest.TestCase):
    def test_owner_fails_closed_without_github_write_capability(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "requires an authorized GitHub write capability"):
                owner._credentials()


if __name__ == "__main__":
    unittest.main()
