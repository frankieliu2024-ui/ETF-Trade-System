import unittest

import numpy as np
import pandas as pd

from research.backtests.index_futures_expiry_aware_validation import (
    _mark_selection,
    build_contract_panel,
)


class ExpiryAwarePanelTest(unittest.TestCase):
    def _inputs(self):
        dates = pd.to_datetime([
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
            "2026-01-09",
            "2026-01-12",
            "2026-01-13",
        ])
        rows = []
        for i, date in enumerate(dates):
            rows.append(
                {
                    "trade_date": date,
                    "product": "IF",
                    "contract": "IF2601",
                    "expiry_date": pd.Timestamp("2026-01-16"),
                    "close": 4000 + i,
                    "settlement": 4000 + i,
                    "volume": 100 - i,
                    "open_interest": 1000 - 100 * i,
                }
            )
            rows.append(
                {
                    "trade_date": date,
                    "product": "IF",
                    "contract": "IF2602",
                    "expiry_date": pd.Timestamp("2026-02-20"),
                    "close": 4040 + i,
                    "settlement": 4040 + i,
                    "volume": 50 + 20 * i,
                    "open_interest": 200 + 200 * i,
                }
            )
        futures = pd.DataFrame(rows)
        spot = pd.DataFrame(
            {"trade_date": dates, "product": "IF", "spot_close": [4010.0] * len(dates)}
        )
        return futures, spot

    def test_main_selection_uses_same_day_open_interest(self):
        futures, spot = self._inputs()
        panel = build_contract_panel(futures, spot)
        main = _mark_selection(panel, "main")
        by_date = dict(zip(main["trade_date"].dt.strftime("%Y-%m-%d"), main["contract"]))
        self.assertEqual(by_date["2026-01-05"], "IF2601")
        self.assertEqual(by_date["2026-01-09"], "IF2602")
        self.assertTrue(main["roll_flag"].any())

    def test_five_day_change_is_contract_local_not_spliced(self):
        futures, spot = self._inputs()
        panel = build_contract_panel(futures, spot)
        main = _mark_selection(panel, "main")
        first_new_main = main.loc[main["roll_flag"]].iloc[0]
        contract_row = panel.loc[
            (panel["trade_date"] == first_new_main["trade_date"])
            & (panel["contract"] == first_new_main["contract"])
        ].iloc[0]
        # The feature follows the selected contract's own history. It is never
        # recomputed from the previous selected contract, so roll itself cannot
        # manufacture the signal.
        expected = contract_row["basis_close_pct_change_5d"]
        actual = first_new_main["basis_close_pct_change_5d"]
        if np.isnan(expected):
            self.assertTrue(np.isnan(actual))
        else:
            self.assertAlmostEqual(actual, expected)

    def test_near_contract_is_earliest_unexpired(self):
        futures, spot = self._inputs()
        panel = build_contract_panel(futures, spot)
        near = _mark_selection(panel, "near")
        self.assertTrue((near["contract"] == "IF2601").all())


if __name__ == "__main__":
    unittest.main()
