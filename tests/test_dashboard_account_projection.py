from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import process_state_sync_request as process_sync
from scripts import sync_formal_files as formal_sync
from scripts.build_stock_context import active_account_asset_codes
POSITIONS=[
{"code":"300750","name":"宁德时代","quantity":100,"cost":393.781,"current_price":351.0,"market_value":35100,"pnl":-4278.07,"pnl_pct":-10.864},
{"code":"601138","name":"工业富联","quantity":500,"cost":58.054,"current_price":63.69,"market_value":31845,"pnl":2817.78,"pnl_pct":9.707},
{"code":"561980","name":"半导体设备ETF","quantity":38900,"cost":.847,"current_price":.644,"market_value":25051.6,"pnl":-7913.3,"pnl_pct":-24.005},
{"code":"588000","name":"科创50ETF","quantity":14100,"cost":2.099,"current_price":1.668,"market_value":23518.8,"pnl":-6075.61,"pnl_pct":-20.53},
{"code":"159941","name":"纳指ETF","quantity":12200,"cost":1.626,"current_price":1.664,"market_value":20300.8,"pnl":464.2,"pnl_pct":2.34},
{"code":"159781","name":"科创创业ETF","quantity":19500,"cost":1.269,"current_price":1.032,"market_value":20124,"pnl":-4624.6,"pnl_pct":-18.686},
{"code":"301689","name":"电科思仪","quantity":500,"cost":16,"current_price":16,"market_value":8000,"pnl":0,"pnl_pct":0},
{"code":"159326","name":"电网设备ETF","quantity":3000,"cost":1.653,"current_price":1.643,"market_value":4929,"pnl":-29,"pnl_pct":-.585},
{"code":"518880","name":"黄金ETF","quantity":500,"cost":9.118,"current_price":9.164,"market_value":4582,"pnl":22.95,"pnl_pct":.503},]
class DashboardAccountProjectionTests(unittest.TestCase):
 def setUp(self):
  self.account={"status":"VALID","updated_at":"2026-09-04T15:03:00+08:00","source":"TEST","total_asset":180568.42,"stock_market_value":74945,"cash":6470.62,"holding_pnl":-18969.05,"daily_pnl":1282.95,"daily_pnl_pct":.71,"positions":POSITIONS}
  self.equity={"summary":{"unknown_fee_flag":False}}
  self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); p=self.root/"config/market/etf_monitor_universe.json"; p.parent.mkdir(parents=True); p.write_text(json.dumps({"objects":[{"code":x} for x in ("561980","588000","159941","159781","159326","518880")]},ensure_ascii=False),encoding="utf-8")
 def tearDown(self): self.temp.cleanup()
 def test_current_schema_both_builders(self):
  membership=active_account_asset_codes(self.root,self.account); self.assertEqual(membership["stocks"],{"300750","601138","301689"}); self.assertEqual(len(membership["etf"]),6)
  with patch.object(formal_sync,"ROOT",self.root),patch.object(process_sync,"ROOT",self.root):
   a=formal_sync.build_dashboard_block(self.account,self.equity,"",self.root); b=process_sync.build_dashboard_block(self.account,None,{"interaction_scenario":"ACCOUNT_CONFIRMATION"})
  for rendered in (a,b):
   self.assertIn("351.000",rendered); self.assertIn("-4,278.07元",rendered); self.assertIn("0.644",rendered); self.assertIn("-7,913.30元",rendered); self.assertIn("9.164",rendered); self.assertIn("22.95元",rendered); self.assertNotIn("持仓ETF：无",rendered); self.assertNotIn("账户个股：无",rendered)
   self.assertIn("观察ETF：", rendered)
 def test_legacy_schema_fallback(self):
  legacy=json.loads(json.dumps(self.account))
  for p in legacy["positions"]: p["asset_type"]="ETF" if "ETF" in p["name"] else "STOCK"; p["last_price"]=p.pop("current_price"); p["holding_pnl"]=p.pop("pnl"); p["holding_pnl_pct"]=p.pop("pnl_pct")
  with patch.object(formal_sync,"ROOT",self.root): rendered=formal_sync.build_dashboard_block(legacy,self.equity,"",self.root)
  self.assertIn("351.000",rendered); self.assertIn("-4,278.07元",rendered)

 def test_formal_comparison_keeps_event_and_state_channels(self):
  context = {"as_of_beijing":"2026-09-04T15:06:26+08:00", "items":[
   {"code":"561980","as_of_beijing":"2026-09-04T15:06:26+08:00","historical_context":{"status":"READY","latest_history_date":"2026-09-03","return_vs_20_sessions_ago_pct":-10.9,"trend_state":"FALLING_TREND","window_20":{"position":0.2}},"turnover_acceptance_context":{"status":"READY","acceptance_behavior":"TURNOVER_NEUTRAL"},"participation_structure_confirmation":{"status":"READY","completed_bar_date":"2026-09-04","participation_ratio_vs_prior_20d":0.88,"enhancement_active":False}},
  ]}
  delta = {"items":[{"code":"561980","delta_from_prior_research_node":{"direction":"IMPROVED"}}]}
  (self.root/"data/state").mkdir(parents=True)
  (self.root/"data/state/market_structure_context.json").write_text(json.dumps(context),encoding="utf-8")
  (self.root/"data/state/research_evidence_delta.json").write_text(json.dumps(delta),encoding="utf-8")
  snapshot={"captured_at_beijing":"2026-09-04T15:06:35+08:00","market_phase":"POST_CLOSE_GRACE","rows":[{"symbol":"561980","quality_status":"PASS","as_of_beijing":"2026-09-04T15:06:35+08:00","close":.644,"change_pct":-2.7}]}
  with patch.object(process_sync,"ROOT",self.root):
   out=process_sync.build_comparison_snapshot(snapshot)
  self.assertEqual(out["etf_count"],1)
  self.assertEqual(out["state_persistence"]["identity_set"], ["561980","588000","159941","159781","159326","518880"])
  item=out["state_persistence"]["items"][0]
  self.assertEqual(item["status"],"READY")
  self.assertEqual(item["historical"]["trend_state"],"FALLING_TREND")
  self.assertEqual(item["event_delta"]["delta_from_prior_research_node"]["direction"],"IMPROVED")

 def test_state_projection_is_explicit_for_missing_and_future_context(self):
  context={"as_of_beijing":"2026-09-04T15:06:40+08:00","items":[{"code":"561980","as_of_beijing":"2026-09-04T15:06:40+08:00","historical_context":{"trend_state":"FUTURE"}}]}
  (self.root/"data/state").mkdir(parents=True)
  (self.root/"data/state/market_structure_context.json").write_text(json.dumps(context),encoding="utf-8")
  (self.root/"data/state/research_evidence_delta.json").write_text(json.dumps({"items":[]}),encoding="utf-8")
  snapshot={"captured_at_beijing":"2026-09-04T15:06:35+08:00","rows":[]}
  with patch.object(process_sync,"ROOT",self.root):
   out=process_sync.build_comparison_snapshot(snapshot)
  statuses={x["code"]:x["status"] for x in out["state_persistence"]["items"]}
  self.assertEqual(statuses["561980"],"STALE_OR_UNVERIFIABLE")
  self.assertEqual(statuses["588000"],"MISSING")
if __name__=="__main__": unittest.main()
