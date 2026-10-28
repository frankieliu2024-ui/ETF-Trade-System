from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import scheduled_review_completion_owner as owner


class TradeReviewCompletionOwnerTests(unittest.TestCase):
    def test_owner_exposes_persistence_and_notification_operations(self):
        self.assertTrue(callable(owner.persist_completion))
        self.assertTrue(callable(owner.dispatch_notification))


if __name__ == "__main__":
    unittest.main()
