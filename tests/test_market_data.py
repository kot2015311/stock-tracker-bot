import unittest
from datetime import date

from helpers import make_candles_json  # noqa: F401  (добавляет папку проекта в sys.path)

from market_data import parse_candles, parse_cbr_rates, parse_dividends, parse_futures, parse_key_rate

DIVIDENDS = {
    "dividends": {
        "columns": ["secid", "isin", "registryclosedate", "value", "currencyid"],
        "data": [
            ["SBER", "RU0009029540", "2024-07-11", 33.3, "SUR"],
            ["SBER", "RU0009029540", "2025-07-18", 34.84, "RUB"],
            ["SBER", "RU0009029540", "2023-07-21", None, "RUB"],
        ],
    }
}

CBR = {
    "Date": "2026-10-17T11:30:00+03:00",
    "Valute": {
        "USD": {"Nominal": 1, "Value": 80.0, "Previous": 79.0},
        "CNY": {"Nominal": 10, "Value": 110.0, "Previous": 100.0},
    },
}

FUTURES = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "ASSETCODE", "LASTTRADEDATE", "PREVSETTLEPRICE"],
        "data": [
            ["BRV6", "BR-10.26", "BR", "2026-10-01", 100.0],  # уже истёк
            ["BRX6", "BR-11.26", "BR", "2026-11-02", 112.9],
            ["BRZ6", "BR-12.26", "BR", "2026-12-01", 111.5],
            ["SiZ6", "Si-12.26", "Si", "2026-12-17", 82000.0],
        ],
    },
    "marketdata": {
        "columns": ["SECID", "LAST", "OPENPOSITION"],
        "data": [
            ["BRV6", None, 0],
            ["BRX6", 113.7, 250000],
            ["BRZ6", 112.2, 40000],
            ["SiZ6", 81500.0, 900000],
        ],
    },
}

KEY_RATE_HTML = """
<table class="data"><tbody>
<tr><th>Дата</th><th>Ставка</th></tr>
<tr><td>23.10.2026</td><td>14,00</td></tr>
<tr><td>22.10.2026</td><td>14,00</td></tr>
<tr><td>27.07.2026</td><td>14,00</td></tr>
<tr><td>25.07.2026</td><td>15,00</td></tr>
<tr><td>24.07.2026</td><td>15,00</td></tr>
</tbody></table>
"""


class MarketDataTest(unittest.TestCase):
    def test_candles_sorted(self):
        candles = parse_candles(make_candles_json(count=5))
        self.assertEqual(len(candles), 5)
        self.assertLess(candles[0].day, candles[-1].day)
        self.assertAlmostEqual(candles[-1].close, 104.0)

    def test_dividends_sorted_and_empty_values_skipped(self):
        result = parse_dividends(DIVIDENDS)
        self.assertEqual([d.record_date for d in result], ["2025-07-18", "2024-07-11"])
        self.assertEqual(result[1].currency, "RUB")  # SUR показываем как RUB

    def test_dividends_error_lists_blocks(self):
        with self.assertRaises(ValueError) as ctx:
            parse_dividends({"other": {}})
        self.assertIn("other", str(ctx.exception))

    def test_cbr_nominal(self):
        rates = parse_cbr_rates(CBR)
        self.assertEqual(rates.date, "2026-10-17")
        self.assertAlmostEqual(rates.rates["CNY"][0], 11.0)  # 110 за 10 юаней
        self.assertNotIn("EUR", rates.rates)

    def test_futures_main_contract_by_open_interest(self):
        quote = parse_futures(FUTURES, "BR", date(2026, 10, 23))
        self.assertEqual(quote.secid, "BRX6")
        self.assertAlmostEqual(quote.price, 113.7)
        self.assertAlmostEqual(quote.change_pct, (113.7 - 112.9) / 112.9 * 100)

    def test_futures_without_market_data_uses_settle_price_and_nearest(self):
        data = {"securities": FUTURES["securities"]}
        quote = parse_futures(data, "BR", date(2026, 10, 23))
        self.assertEqual(quote.secid, "BRX6")
        self.assertAlmostEqual(quote.price, 112.9)

    def test_futures_absent(self):
        self.assertIsNone(parse_futures({"securities": {"columns": [], "data": []}}, "BR"))

    def test_key_rate_with_change(self):
        rate = parse_key_rate(KEY_RATE_HTML)
        self.assertEqual(rate.value, 14.0)
        self.assertEqual(rate.since, "27.07.2026")
        self.assertEqual(rate.previous, 15.0)

    def test_key_rate_missing_table(self):
        with self.assertRaises(ValueError):
            parse_key_rate("<html>капча</html>")


if __name__ == "__main__":
    unittest.main()
