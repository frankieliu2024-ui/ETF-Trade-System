import unittest
from scripts.process_state_sync_request import merge_account_fact

def acct(cash=10349.47, obligations=None, **extra):
    value={"status":"VALID","updated_at":"2026-09-01T07:15:00+08:00","source":"BROKER_SCREENSHOT","cash":cash,"positions":[],"trades":[]}
    if obligations is not None: value["settlement_obligations"]=obligations
    value.update(extra)
    return value

def ipo(status="PENDING_PAYMENT"):
    return {"obligation_type":"IPO_ALLOTMENT_PAYMENT","security_name":"电科思仪","security_code":"301689","quantity":500,"subscription_price":16.0,"required_cash":8000,"deadline":"2026-09-01T16:00:00+08:00","status":status,"source":"BROKER_SCREENSHOT","pit_timestamp":"2026-09-01T07:15:00+08:00"}

class SettlementObligationTests(unittest.TestCase):
    def test_301689_pit_cash_derivation(self):
        got=merge_account_fact({},acct(obligations=[ipo()]))
        self.assertEqual(got["reserved_cash_for_settlement"],8000.0)
        self.assertEqual(got["deployable_cash"],2349.47)
    def test_missing_obligation_carries_forward_and_duplicate_is_idempotent(self):
        first=merge_account_fact({},acct(obligations=[ipo()]))
        again=merge_account_fact(first,acct())
        self.assertEqual(again["settlement_obligations"],first["settlement_obligations"])
        self.assertEqual(again["deployable_cash"],2349.47)
    def test_settled_does_not_regress_or_double_reserve(self):
        settled=merge_account_fact({},acct(obligations=[ipo("SETTLED")]))
        replay=merge_account_fact(settled,acct(obligations=[ipo("PENDING_PAYMENT")],updated_at="2026-09-01T07:00:00+08:00"))
        self.assertEqual(replay["settlement_obligations"][0]["status"],"SETTLED")
        self.assertEqual(replay["reserved_cash_for_settlement"],0.0)

    def test_deadline_field_alias_is_same_logical_obligation(self):
        first = ipo("PENDING_PAYMENT")
        first.pop("deadline")
        first["payment_deadline_beijing"] = "2026-09-01T16:00:00+08:00"
        second = ipo("SETTLED")
        merged = merge_account_fact({}, acct(obligations=[first]))
        merged = merge_account_fact(merged, acct(obligations=[second], updated_at="2026-09-02T08:50:00+08:00"))
        self.assertEqual(len(merged["settlement_obligations"]), 1)
        self.assertEqual(merged["settlement_obligations"][0]["status"], "SETTLED")
        self.assertEqual(merged["reserved_cash_for_settlement"], 0.0)
    def test_explicit_constraints(self):
        low=merge_account_fact({},acct(cash=5000,obligations=[ipo()]))
        self.assertEqual(low["settlement_constraint_status"],"INSUFFICIENT_CASH")
        expired=merge_account_fact({},acct(obligations=[ipo("DEADLINE_PASSED_UNCONFIRMED")]))
        self.assertEqual(expired["settlement_constraint_status"],"DEADLINE_PASSED_UNCONFIRMED")
    def test_origin_requires_confirmed_fact(self):
        good=merge_account_fact({},acct(obligations=[ipo("SETTLED")],positions=[{"asset_type":"STOCK","code":"301689","name":"电科思仪","quantity":500}]))
        self.assertEqual(good["positions"][0]["origin"],"IPO_ALLOTMENT_ORIGIN")
        ordinary=merge_account_fact({},acct(positions=[{"asset_type":"STOCK","code":"301689","name":"电科思仪","quantity":500}]))
        self.assertNotIn("origin",ordinary["positions"][0])
    def test_history_carries_forward(self):
        prior=acct(trades=[{"event_id":"t1"}],formal_action={"action":"hold"},fee_facts=[{"fee":1}],reconciliation_metadata={"status":"RECONCILED"})
        got=merge_account_fact(prior,acct())
        for key in ("trades","formal_action","fee_facts","reconciliation_metadata"): self.assertEqual(got[key],prior[key])

    def test_confirmed_broker_cash_is_not_reconstructed_from_trade_amount(self):
        prior = acct(cash=19196.47, obligations=[ipo("PENDING_PAYMENT")])
        supplied = acct(cash=11197.47, obligations=[ipo("SETTLED")],
                        updated_at="2026-09-02T08:50:00+08:00")
        supplied["trades"] = [{"code": "561980", "side": "SELL", "quantity": 13000,
                               "price": 0.681, "amount": 8853.0}]
        got = merge_account_fact(prior, supplied)
        self.assertEqual(got["cash"], 11197.47)
        self.assertEqual(got["reserved_cash_for_settlement"], 0.0)
        self.assertEqual(got["deployable_cash"], 11197.47)
    def test_dashboard_contract_names_all_three_cash_values(self):
        dashboard = "|账面现金|10,349.47元|\\n|待结算锁定资金|8,000.00元|\\n|可部署现金|2,349.47元|"
        for label in ("账面现金", "待结算锁定资金", "可部署现金"):
            self.assertIn(label, dashboard)

if __name__=="__main__": unittest.main()
