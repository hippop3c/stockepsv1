import datetime as dt
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

# Pure parser/merge tests do not make HTTP requests. Keep them runnable in the
# minimal Codex runtime even when the requests wheel is not installed there.
sys.modules.setdefault("requests", types.SimpleNamespace())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import finmind_fetch
import update_price
import update_target_price


class EpsTests(unittest.TestCase):
    def test_non_company_codes_are_excluded(self):
        self.assertFalse(finmind_fetch.is_common_stock("0050"))
        self.assertFalse(finmind_fetch.is_common_stock("9103"))
        self.assertTrue(finmind_fetch.is_common_stock("2330"))

    def test_finmind_eps_is_not_differenced(self):
        rows = [
            {"date": "2024-03-31", "type": "EPS", "value": 8.70},
            {"date": "2024-06-30", "type": "EPS", "value": 9.56},
            {"date": "2024-06-30", "type": "Revenue", "value": 1},
        ]
        self.assertEqual(
            finmind_fetch.eps_rows(rows),
            [
                {"date": "2024-03-31", "single": 8.7, "cum": 8.7},
                {"date": "2024-06-30", "single": 9.56, "cum": 9.56},
            ],
        )

    def test_old_bad_single_is_repaired_and_overlap_replaced(self):
        old = [
            {"date": "2024-03-31", "cum": 8.7, "single": 8.7},
            {"date": "2024-06-30", "cum": 9.56, "single": 0.86},
        ]
        fresh = [{"date": "2024-06-30", "cum": 9.6, "single": 9.6}]
        self.assertEqual(
            finmind_fetch.merge_eps(old, fresh, "2024-01-01"),
            [
                {"date": "2024-03-31", "cum": 8.7, "single": 8.7},
                {"date": "2024-06-30", "cum": 9.6, "single": 9.6},
            ],
        )

    def test_universe_keeps_cached_market_when_one_source_is_truncated(self):
        rows_by_url = {
            finmind_fetch.TWSE: [{"Code": "1101", "Name": "台泥"}],
            finmind_fetch.TPEX_MAIN: [],
            finmind_fetch.TPEX_ESB: [
                {"SecuritiesCompanyCode": "7777", "CompanyName": "測試興櫃"}
            ],
        }

        class Response:
            def __init__(self, rows):
                self.rows = rows

            def raise_for_status(self):
                return None

            def json(self):
                return self.rows

        existing = {
            "active_stock_ids": ["1101", "6488", "7777"],
            "market": {"1101": "上市", "6488": "上櫃", "7777": "興櫃"},
            "name": {"6488": "環球晶"},
            "industry": {},
        }
        limits = {"上市": 1, "上櫃": 1, "興櫃": 1}
        fake_get = lambda url, **kwargs: Response(rows_by_url[url])
        with (
            mock.patch.object(finmind_fetch, "MIN_UNIVERSE", 3),
            mock.patch.object(finmind_fetch, "MIN_MARKET_COUNTS", limits),
            mock.patch.object(finmind_fetch.requests, "get", side_effect=fake_get, create=True),
        ):
            universe = finmind_fetch.current_universe([], existing)

        self.assertEqual(set(universe), {"1101", "6488", "7777"})
        self.assertEqual(universe["6488"]["market"], "上櫃")


class PriceTests(unittest.TestCase):
    def test_zero_quote_is_treated_as_missing(self):
        self.assertIsNone(update_price.num("0.00"))
        self.assertEqual(update_price.num("123.5"), 123.5)

    def test_roc_trading_date(self):
        self.assertEqual(update_price.trading_date("1150818"), "2026-08-18")
        self.assertEqual(update_price.trading_date("2026-08-18"), "2026-08-18")
        self.assertIsNone(update_price.trading_date("--"))

    def test_history_replaces_same_day_and_prunes(self):
        max_date = dt.date.today().isoformat()
        old_date = (dt.date.today() - dt.timedelta(days=121)).isoformat()
        old = {"2330": [{"date": old_date, "c": 1}, {"date": max_date, "c": 2}]}
        fresh = {"2330": {"date": max_date, "o": 3, "h": 4, "l": 2, "c": 4}}
        merged = update_price.merge_history(old, fresh, max_date)
        self.assertEqual(merged["2330"], [fresh["2330"]])

    def test_history_prunes_inactive_codes(self):
        max_date = dt.date.today().isoformat()
        row = {"date": max_date, "o": 1, "h": 2, "l": 1, "c": 2}
        merged = update_price.merge_history({"9103": [row]}, {"2330": row}, max_date, {"2330"})
        self.assertEqual(merged, {"2330": [row]})


class TargetPriceTests(unittest.TestCase):
    def test_public_rating_table_parser(self):
        html = """
        <table><tr><th>other</th></tr></table>
        <table>
          <tr><th>評等日期</th><th>券商</th><th>原評等</th><th>升/降</th>
              <th>新評等</th><th>財測EPS(年度)</th><th>目標價</th><th>現價</th><th>備註</th></tr>
          <tr><td>20260716</td><td>Factset</td><td></td><td>--</td>
              <td>強力買進</td><td>--</td><td>3,090</td><td>306.5</td><td>--</td></tr>
          <tr><td>20260707</td><td>Factset</td><td></td><td>--</td>
              <td>買進</td><td>--</td><td>--</td><td>306.5</td><td>--</td></tr>
        </table>
        """
        rows = update_target_price.parse_target_rows(html, "2330")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-07-16")
        self.assertEqual(rows[0]["target"], 3090.0)
        self.assertEqual(rows[0]["broker"], "Factset")

    def test_rating_page_must_contain_expected_table(self):
        found, rows = update_target_price.parse_target_page(
            "<html><title>verification</title><body>please wait</body></html>", "2330"
        )
        self.assertFalse(found)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
