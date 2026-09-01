from __future__ import annotations

import unittest

from scripts.process_state_sync_request import merge_account_fact


def account(cash=10349.47, obligations=None, **extra):
    value = {
        "status": "VALID",
        "updated_at": "2026-09-01T07:15:00+08:00",
        "source": "BROKER_SCREENSHOT_20260901_0715_USER_PROVIDED",
        "cash": cash,
        "positions": [],
        "trades": [],
    }
    if obligations is not None:
        value["settlement_obligations"] = obligations
    value.update(extra)
    return value


def ipo_obligation(status="PENDING_PAYMENT"):
    return {
        "obligation_type": "IPO_ALLOTMENT_PAYMENT",
        "security_name": "电科思仪",
        "security_code": "301689",
        "quantity": 500,
        "subscription_price": 16.0,
        "required_cash": 8000,
        "deadline": "2026-09-01T16:00:00+08:00",
        "status": status,
        "source": "BROKER_IPO_ALLOTMENT_SCREENSHOT_AND_SMS",
        "pit_timestamp": "2026-09-01T07:15:00+08:00",
    }


class SettlementObligationTests(unittest.TestCase):
    def test_pit_replay_derives_reserved_and_deployable_cash(self):
        got = merge_account_fact({}, account(obligations=[ipo_obligation()]))
        self.assertEqual(got["reserved_cash_for_settlement"], 8000.0)
        self.assertEqual(got["deployable_cash"], 2349.47)

    def test_duplicate_and_missing_obligation_are_idempotent(self):
        first = merge_account_fact({}, account(obligations=[ipo_obligation()]))
        again = merge_account_fact(first, account())
        self.assertEqual(again["settlement_obligations"], first["settlement_obligations"])
        self.assertEqual(again["deployable_cash"], first["deployable_cash"])

    def test_settled_cannot_regress_to_pending(self):
        settled = merge_account_fact({}, account(obligations=[ipo_obligation("SETTLED")]))
        stale = merge_account_fact(
            settled,
            account(updated_at="2026-09-01T07:00:00+08:00",
                    obligations=[ipo_obligation("PENDING_PAYMENT")]),
        )
        self.assertEqual(stale["settlement_obligations"][0]["status"], "SETTLED")
        self.assertEqual(stale["reserved_cash_for_settlement"], 0.0)

    def test_settled_releases_reserve_once(self):
        pending = merge_account_fact({}, account(obligations=[ipo_obligation()]))
        settled = merge_account_fact(pending, account(obligations=[ipo_obligation("SETTLED")]))
        replay = merge_account_fact(settled, account(obligations=[ipo_obligation("SETTLED")]))
        self.assertEqual(settled["reserved_cash_for_settlement"], 0.0)
        self.assertEqual(replay["reserved_cash_for_settlement"], 0.0)

    def test_insufficient_cash_and_deadline_are_explicit(self):
        low = merge_account_fact({}, account(cash=5000, obligations=[ipo_obligation()]))
        self.assertEqual(low["settlement_constraint_status"], "INSUFFICIENT_CASH")
        expired = merge_account_fact({}, account(obligations=[ipo_obligation("DEADLINE_PASSED_UNCONFIRMED")]))
        self.assertEqual(expired["settlement_constraint_status"], "DEADLINE_PASSED_UNCONFIRMED")

    def test_origin_requires_confirmed_allotment_fact(self):
        with_origin = merge_account_fact(
            {},
            account(
                obligations=[ipo_obligation("SETTLED")],
                positions=[{"asset_type": "STOCK", "code": "301689", "name": "电科思仪", "quantity": 500}],
            ),
        )
        self.assertEqual(with_origin["positions"][0]["origin"], "IPO_ALLOTMENT_ORIGIN")
        ordinary = merge_account_fact(
            {},
            account(positions=[{"asset_type": "STOCK", "code": "301689", "name": "电科思仪", "quantity": 500}]),
        )
        self.assertNotIn("origin", ordinary["positions"][0])

    def test_trade_and_reconciliation_history_carries_forward(self):
        prior = account(
            trades=[{"event_id": "t1"}],
            formal_action={"action": "hold"},
            fee_facts=[{"fee": 1}],
            reconciliation_metadata={"status": "RECONCILED"},
        )
        got = merge_account_fact(prior, account())
        self.assertEqual(got["trades"], prior["trades"])
        self.assertEqual(got["formal_action"], prior["formal_action"])
        self.assertEqual(got["fee_facts"], prior["fee_facts"])
        self.assertEqual(got["reconciliation_metadata"], prior["reconciliation_metadata"])


if __name__ == "__main__":
    unittest.main()
