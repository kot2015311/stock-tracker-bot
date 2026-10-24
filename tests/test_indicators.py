import unittest
from datetime import date, timedelta

from helpers import make_candles_json

from indicators import atr, compute_stats, describe, rsi, sma
from market_data import Candle, parse_candles

# Классический пример RSI(14) с сайта StockCharts. У них 70.53 из-за округления средних (0.24 и 0.10),
# точный расчёт даёт 70.46.
WILDER_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
                 45.89, 46.03, 45.61, 46.28, 46.28]


class IndicatorsTest(unittest.TestCase):
    def test_sma(self):
        self.assertAlmostEqual(sma([1, 2, 3, 4], 2), 3.5)
        self.assertIsNone(sma([1, 2], 3))

    def test_rsi_reference_value(self):
        self.assertAlmostEqual(rsi(WILDER_CLOSES, 14), 70.46, delta=0.01)

    def test_rsi_extremes(self):
        self.assertEqual(rsi(list(range(1, 30)), 14), 100.0)
        self.assertLess(rsi(list(range(30, 1, -1)), 14), 1.0)
        self.assertIsNone(rsi([1, 2, 3], 14))

    def test_atr_constant_range(self):
        day = date(2026, 1, 1)
        candles = [Candle(day + timedelta(days=i), 100, 101, 99, 100, 1000) for i in range(30)]
        self.assertAlmostEqual(atr(candles, 14), 2.0)

    def test_stats_short_history(self):
        stats = compute_stats(parse_candles(make_candles_json(count=30)))
        self.assertAlmostEqual(stats.last_close, 129.0)
        self.assertAlmostEqual(stats.day_pct, (129 - 128) / 128 * 100)
        self.assertAlmostEqual(stats.week_pct, (129 - 124) / 124 * 100)
        self.assertAlmostEqual(stats.month_pct, (129 - 109) / 109 * 100)
        self.assertAlmostEqual(stats.low_20, 109.0)
        self.assertAlmostEqual(stats.high_20, 130.0)
        self.assertAlmostEqual(stats.volume_ratio, 2.0)
        self.assertAlmostEqual(stats.prev_volume_ratio, 1.0)
        self.assertIsNone(stats.sma200)
        self.assertIsNone(stats.high_52w)
        self.assertIsNone(compute_stats(parse_candles(make_candles_json(count=1))))

    def test_stats_long_history(self):
        stats = compute_stats(parse_candles(make_candles_json(count=260)))
        self.assertIsNotNone(stats.sma200)
        self.assertAlmostEqual(stats.sma200, sum(100 + i for i in range(60, 260)) / 200)
        self.assertEqual(stats.high_52w, 100 + 259 + 1)
        self.assertEqual(stats.rsi14, 100.0)

    def test_describe_facts(self):
        stats = compute_stats(parse_candles(make_candles_json(count=260)))
        facts = "; ".join(describe(stats))
        self.assertIn("выше 200-дневной средней", facts)
        self.assertIn("перекупленности", facts)
        self.assertIn("годового максимума", facts)
        self.assertIn("ATR14", facts)
        falling = compute_stats(parse_candles(make_candles_json(count=260, base=400, step=-1)))
        facts = "; ".join(describe(falling))
        self.assertIn("ниже 200-дневной средней", facts)
        self.assertIn("перепроданности", facts)


if __name__ == "__main__":
    unittest.main()
