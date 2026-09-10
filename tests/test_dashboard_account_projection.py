from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import process_state_sync_request as process_sync
from scripts import sync_formal_files as formal_sync
from scripts.build_stock_context import active_account_asset_codes, build_managed_position_projection
from scripts import build_stock_context as stock_context_builder
from scripts import build_account_stock_market_legacy as stock_market_builder
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

 def test_confirmed_ipo_allotment_origin_enters_default_stock_market_chain(self):
  account={"status":"VALID","positions":[
   {"asset_type":"STOCK","code":"301689","name":"电科思仪","quantity":500,
    "origin":"IPO_ALLOTMENT_ORIGIN"},
   {"asset_type":"STOCK","code":"300750","name":"宁德时代","quantity":100},
   {"asset_type":"STOCK","code":"601138","name":"工业富联","quantity":500},
   {"asset_type":"ETF","code":"561980","name":"半导体设备ETF","quantity":38900},
   {"asset_type":"STOCK","code":"000001","name":"未知个股","quantity":100},
  ]}
  roles={"roles":{"601138":{"role":"IPO_BASE_STOCK","status":"CONFIRMED"}}}
  (self.root/"config/market/stock_monitor_policy.json").write_text("{}",encoding="utf-8")
  (self.root/"data/state/asset_roles.json").write_text(json.dumps(roles),encoding="utf-8")
  with patch.object(stock_context_builder,"ROOT",self.root), \
       patch.object(stock_context_builder,"POLICY_PATH",self.root/"config/market/stock_monitor_policy.json"), \
       patch.object(stock_context_builder,"ROLE_PATH",self.root/"data/state/asset_roles.json"), \
       patch.object(stock_context_builder,"read_account_fact",return_value=account):
   context=stock_context_builder.build()
  layer=context["default_stock_layer"]
  self.assertEqual([x["code"] for x in layer["ipo_allotment_stocks"]],["301689"])
  self.assertEqual([x["code"] for x in layer["ipo_base_stocks"]],["601138"])
  self.assertEqual({x["code"] for x in layer["monitored_account_stocks"]},{"301689","601138"})
  self.assertEqual([x["code"] for x in layer["unclassified_stocks"]],["300750","000001"])
  self.assertTrue(context["needs_role_confirmation"])
  context_path=self.root/"data/state/stock_context.json"
  context_path.write_text(json.dumps(context),encoding="utf-8")
  with patch.object(stock_market_builder,"STOCK_CONTEXT",context_path), \
       patch.object(stock_market_builder,"fetch_tencent_many",return_value={
        "301689":{"code":"301689","quality_status":"PASS"},
        "601138":{"code":"601138","quality_status":"PASS"},
       }), patch.object(stock_market_builder.shutil,"which",return_value=None):
   market=stock_market_builder.build()
  self.assertEqual(set(market["objects"]),{"301689","601138"})

 def test_ipo_allotment_quantity_zero_is_not_monitored(self):
  account={"status":"VALID","positions":[
   {"asset_type":"STOCK","code":"301689","name":"电科思仪","quantity":0,
    "origin":"IPO_ALLOTMENT_ORIGIN"},
  ]}
  (self.root/"config/market/stock_monitor_policy.json").write_text("{}",encoding="utf-8")
  (self.root/"data/state/asset_roles.json").write_text(json.dumps({"roles":{}}),encoding="utf-8")
  with patch.object(stock_context_builder,"ROOT",self.root), \
       patch.object(stock_context_builder,"POLICY_PATH",self.root/"config/market/stock_monitor_policy.json"), \
       patch.object(stock_context_builder,"ROLE_PATH",self.root/"data/state/asset_roles.json"), \
       patch.object(stock_context_builder,"read_account_fact",return_value=account):
   layer=stock_context_builder.build()["default_stock_layer"]
  self.assertEqual(layer["monitored_account_stocks"],[])
  self.assertEqual(layer["ipo_allotment_stocks"],[])

 def test_formal_comparison_preserves_event_delta_and_state_persistence_for_buy_and_sell_consumers(self):
  context = {"status":"READY", "as_of_beijing":"2026-09-04T15:06:26+08:00", "items":[
   {"code":"561980","as_of_beijing":"2026-09-04T15:06:26+08:00",
    "historical_context":{"latest_history_date":"2026-09-03","trend_state":"FALLING_TREND"},
    "turnover_acceptance_context":{"status":"READY","acceptance_behavior":"TURNOVER_NEUTRAL"},
    "participation_structure_confirmation":{"status":"READY","completed_bar_date":"2026-09-04","participation_ratio_vs_prior_20d":0.88,"enhancement_active":False}}
  ]}
  delta = {"as_of_beijing":"2026-09-04T15:06:26+08:00","items":[
   {"code":"561980","delta_from_prior_research_node":{"direction":"IMPROVED"}}
  ]}
  (self.root/"data/state/market_structure_context.json").write_text(json.dumps(context),encoding="utf-8")
  (self.root/"data/state/research_evidence_delta.json").write_text(json.dumps(delta),encoding="utf-8")
  snapshot={"captured_at_beijing":"2026-09-04T15:06:35+08:00","market_phase":"POST_CLOSE_GRACE",
            "rows":[{"symbol":"561980","quality_status":"PASS","as_of_beijing":"2026-09-04T15:06:35+08:00","close":.644,"change_pct":-2.7}]}
  with patch.object(process_sync,"ROOT",self.root):
   out=process_sync.build_comparison_snapshot(snapshot)
  state=out["state_persistence"]
  self.assertEqual(state["identity_set"], ["561980","588000","159941","159781","159326","518880"])
  item=state["items"][0]
  self.assertEqual(item["status"],"READY")
  self.assertEqual(item["historical"]["trend_state"],"FALLING_TREND")
  self.assertEqual(item["event_delta"]["delta_from_prior_research_node"]["direction"],"IMPROVED")
  self.assertFalse(state["consumer_contract"]["buy_candidate"]["can_generate_action_independently"])
  self.assertFalse(state["consumer_contract"]["held_position_sell"]["can_generate_action_independently"])
  self.assertIn("不生成轮动动作", out["interpretation_rule"])

 def test_state_projection_is_fail_safe_for_missing_stale_and_future_context(self):
  context={"status":"READY","as_of_beijing":"2026-09-04T15:06:40+08:00","items":[
   {"code":"561980","as_of_beijing":"2026-09-04T15:06:40+08:00","historical_context":{"trend_state":"FUTURE"}}
  ]}
  (self.root/"data/state/market_structure_context.json").write_text(json.dumps(context),encoding="utf-8")
  (self.root/"data/state/research_evidence_delta.json").write_text(json.dumps({"as_of_beijing":"2026-09-04T15:06:40+08:00","items":[]}),encoding="utf-8")
  snapshot={"captured_at_beijing":"2026-09-04T15:06:35+08:00","rows":[]}
  with patch.object(process_sync,"ROOT",self.root):
   out=process_sync.build_comparison_snapshot(snapshot)
  statuses={x["code"]:x["status"] for x in out["state_persistence"]["items"]}
  self.assertEqual(statuses["561980"],"STALE_OR_UNVERIFIABLE")
  self.assertEqual(statuses["588000"],"MISSING")
  self.assertEqual(out["state_persistence"]["status"],"PARTIAL_FAIL_SAFE")


 def test_state_projection_is_fail_safe_when_context_is_not_ready_at_legal_pit_time(self):
  context={"status":"DEGRADED","as_of_beijing":"2026-09-04T15:06:26+08:00","items":[
   {"code":"561980","as_of_beijing":"2026-09-04T15:06:26+08:00",
    "historical_context":{"trend_state":"FALLING_TREND"},
    "turnover_acceptance_context":{"status":"READY"},
    "participation_structure_confirmation":{"status":"READY","participation_ratio_vs_prior_20d":0.88}}
  ]}
  (self.root/"data/state/market_structure_context.json").write_text(json.dumps(context),encoding="utf-8")
  (self.root/"data/state/research_evidence_delta.json").write_text(json.dumps({"as_of_beijing":"2026-09-04T15:06:26+08:00","items":[]}),encoding="utf-8")
  snapshot={"captured_at_beijing":"2026-09-04T15:06:35+08:00","rows":[]}
  with patch.object(process_sync,"ROOT",self.root):
   out=process_sync.build_comparison_snapshot(snapshot)
  state=out["state_persistence"]
  statuses={x["code"]:x["status"] for x in state["items"]}
  self.assertEqual(statuses["561980"],"STALE_OR_UNVERIFIABLE")
  self.assertEqual(state["status"],"PARTIAL_FAIL_SAFE")
  self.assertEqual(state["items"][0]["historical"],{})
  self.assertEqual(state["items"][0]["participation"]["participation_ratio_vs_prior_20d"],None)

 def test_zero_participation_ratio_is_preserved_over_turnover_fallback(self):
  context={"status":"READY","as_of_beijing":"2026-09-04T15:06:26+08:00","items":[
   {"code":"561980","as_of_beijing":"2026-09-04T15:06:26+08:00",
    "turnover_acceptance_context":{"status":"READY","time_normalized_amount_pace_ratio":0.88},
    "participation_structure_confirmation":{"status":"READY","participation_ratio_vs_prior_20d":0}}
  ]}
  (self.root/"data/state/market_structure_context.json").write_text(json.dumps(context),encoding="utf-8")
  (self.root/"data/state/research_evidence_delta.json").write_text(json.dumps({"as_of_beijing":"2026-09-04T15:06:26+08:00","items":[]}),encoding="utf-8")
  snapshot={"captured_at_beijing":"2026-09-04T15:06:35+08:00","rows":[]}
  with patch.object(process_sync,"ROOT",self.root):
   out=process_sync.build_comparison_snapshot(snapshot)
  item=out["state_persistence"]["items"][0]
  self.assertEqual(item["status"],"READY")
  self.assertEqual(item["participation"]["participation_ratio_vs_prior_20d"],0)
 def test_legacy_schema_fallback(self):
  legacy=json.loads(json.dumps(self.account))
  for p in legacy["positions"]: p["asset_type"]="ETF" if "ETF" in p["name"] else "STOCK"; p["last_price"]=p.pop("current_price"); p["holding_pnl"]=p.pop("pnl"); p["holding_pnl_pct"]=p.pop("pnl_pct")
  with patch.object(formal_sync,"ROOT",self.root): rendered=formal_sync.build_dashboard_block(legacy,self.equity,"",self.root)
  self.assertIn("351.000",rendered); self.assertIn("-4,278.07元",rendered)

 def test_shared_lifecycle_contract_rejects_opportunity_status_for_held_assets(self):
  lifecycle={"电网设备ETF（159326）":"Trial，持有", "黄金ETF（518880）":"Trial，持有"}
  error=process_sync.validate_current_lifecycle_contract(lifecycle,self.account)
  self.assertIn("opportunity lifecycle for a held asset",error)

 def test_post_close_review_uses_same_lifecycle_contract_before_persistence(self):
  review={
   "market_date":"2026-09-07",
   "lifecycle":{"电网设备ETF（159326）":"Trial", "黄金ETF（518880）":"Trial"},
  }
  request={"interaction_scenario":"POST_CLOSE_REVIEW","request_id":"invalid-review","formal_review":review}
  with patch.object(process_sync,"ROOT",self.root):
   with self.assertRaisesRegex(ValueError,"invalid formal review lifecycle contract"):
    process_sync.record_post_close_review(self.account,request)
  self.assertFalse((self.root/"events/reviews/2026-09-07.json").exists())

 def test_shared_lifecycle_contract_accepts_current_actions_and_historical_explanation(self):
  review={
   "market_date":"2026-09-07",
   "lifecycle":{
    "电网设备ETF（159326）":"持有管理（历史Trial来源仅作解释）",
    "黄金ETF（518880）":"降低风险",
    "通信ETF（515880）":"观察",
   },
  }
  error=process_sync.validate_current_lifecycle_contract(review["lifecycle"],self.account)
  self.assertEqual(error,"")

 def test_managed_position_projection_includes_all_positive_etfs_and_stocks_without_asset_type(self):
  projection=build_managed_position_projection(self.root,self.account)
  rows=projection["positions"]
  self.assertEqual(len(rows),9)
  self.assertEqual({x["code"] for x in rows if x["asset_class"]=="ETF"}, {"561980","588000","159941","159781","159326","518880"})
  self.assertEqual({x["code"] for x in rows if x["asset_class"]=="ACCOUNT_STOCK"}, {"300750","601138","301689"})
  self.assertEqual(len({x["code"] for x in rows}),9)
  self.assertEqual(projection["opportunity_scope"],"ETF_UNIVERSE_ONLY")

 def test_managed_position_lifecycle_requires_one_entry_per_current_holding(self):
  complete={f"{p['name']}（{p['code']}）":"持有管理；证据：当前账户事实；动作：继续管理" for p in POSITIONS}
  with patch.object(process_sync,"ROOT",self.root):
   self.assertEqual(process_sync.validate_managed_position_lifecycle(complete,self.account),"")
   incomplete=dict(list(complete.items())[:-1])
   error=process_sync.validate_managed_position_lifecycle(incomplete,self.account,"formal_decision.lifecycle")
  self.assertIn("missing current managed positions",error)

 def test_formal_dashboard_renders_each_managed_position_on_its_own_line(self):
  lifecycle={f"{p['name']}（{p['code']}）":"持有管理；证据：当前账户事实；动作：继续管理" for p in POSITIONS}
  decision={"lifecycle":lifecycle,"risk_permission":"保持","main_candidate":"无新的主候选。"}
  with patch.object(process_sync,"ROOT",self.root):
   rendered=process_sync.build_dashboard_block(self.account,decision,{"interaction_scenario":"INTRADAY"})
  for p in POSITIONS:
   self.assertIn(f"- {p['name']}（{p['code']}）：",rendered)

if __name__=="__main__": unittest.main()
