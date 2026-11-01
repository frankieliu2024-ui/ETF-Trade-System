import unittest

from scripts.build_weekly_return_attribution import _strict_decomposition


def _position(code, quantity, market_value, asset_class="ETF", name=""):
    return {
        "code": code,
        "name": name or code,
        "quantity": quantity,
        "market_value": market_value,
        "asset_class": asset_class,
    }


class StrictWeeklyReturnAttributionTests(unittest.TestCase):
    def test_complete_bridge_for_statement_backed_week(self):
        prior = {
            "positions": [
                _position("159941", 12200, 20349.60),
                _position("159981", 8500, 15708.00),
                _position("159992", 17200, 13932.00),
                _position("127118", 10, 1000.00, "OTHER"),
            ]
        }
        current = {
            "positions": [
                _position("159941", 12200, 20117.80),
                _position("159981", 8500, 16473.00),
                _position("159992", 17200, 13502.00),
                _position("159056", 4700, 4747.00),
                _position("127118", 10, 1000.00, "OTHER"),
            ]
        }
        trades = [{
            "code": "159056",
            "name": "全指器械ETF",
            "side": "BUY",
            "quantity": 4700,
            "price": 1.042,
        }]
        result = _strict_decomposition(prior, current, trades, True, -52.20, 5.00)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["price_change_effect"], -47.20)
        self.assertEqual(result["realized_pnl"], 0.0)
        self.assertEqual(result["unrealized_pnl"], -47.20)
        self.assertEqual(result["fee_effect"], -5.00)
        self.assertEqual(result["other_asset_effect"], 0.0)
        self.assertEqual(result["residual"], 0.0)

    def test_sell_fails_closed_without_cost_basis(self):
        prior = {"positions": [_position("159941", 1000, 1668.00)]}
        current = {"positions": [_position("159941", 500, 824.50)]}
        trades = [{
            "code": "159941",
            "side": "SELL",
            "quantity": 500,
            "price": 1.70,
        }]
        result = _strict_decomposition(prior, current, trades, True, 0.0, 0.0)
        self.assertEqual(result["status"], "NOT_COMPLETE")
        self.assertEqual(result["reason"], "SELL_COST_BASIS_REQUIRED")


if __name__ == "__main__":
    unittest.main()
