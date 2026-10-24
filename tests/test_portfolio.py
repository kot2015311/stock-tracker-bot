import json
import unittest

from helpers import TempDataCase

import config
from portfolio import Position, apply_trade, load_portfolio, portfolio_lines, record_trade, save_portfolio, unrealized


class TradesTest(unittest.TestCase):
    def test_sell_more_than_long_opens_short(self):
        # Ровно случай из жизни: 8 NVTK по 1137, продали 13 по 1124
        pos = Position("NVTK", 8, 1137)
        realized = apply_trade(pos, "sell", 13, 1124)
        self.assertAlmostEqual(realized, -104.0)
        self.assertEqual(pos.qty, -5)
        self.assertEqual(pos.entry, 1124)
        self.assertAlmostEqual(unrealized(pos, 1151.7), -138.5)

    def test_average_price_when_adding(self):
        pos = Position("SBER", 6, 283)
        self.assertEqual(apply_trade(pos, "buy", 4, 293), 0.0)
        self.assertEqual(pos.qty, 10)
        self.assertAlmostEqual(pos.entry, (6 * 283 + 4 * 293) / 10)

    def test_partial_close_keeps_entry(self):
        pos = Position("LKOH", 2, 5900)
        realized = apply_trade(pos, "sell", 1, 6100)
        self.assertAlmostEqual(realized, 200.0)
        self.assertEqual((pos.qty, pos.entry), (1, 5900))

    def test_cover_short_and_flip_to_long(self):
        pos = Position("NVTK", -5, 1124)
        realized = apply_trade(pos, "buy", 7, 1100)
        self.assertAlmostEqual(realized, (1100 - 1124) * 5 * -1)  # шорт заработал 120
        self.assertEqual((pos.qty, pos.entry), (2, 1100))

    def test_full_close(self):
        pos = Position("OZON", 1, 4588)
        apply_trade(pos, "sell", 1, 3520)
        self.assertEqual(pos.qty, 0)
        self.assertIsNone(pos.entry)
        self.assertAlmostEqual(pos.realized, -1068.0)

    def test_new_short_from_zero(self):
        pos = Position("NVTK")
        apply_trade(pos, "sell", 5, 1124)
        self.assertEqual((pos.qty, pos.entry), (-5, 1124))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            apply_trade(Position("X"), "buy", 0, 10)
        with self.assertRaises(ValueError):
            apply_trade(Position("X"), "hold", 1, 10)


class PortfolioFileTest(TempDataCase):
    def test_template_with_nulls_is_ignored(self):
        config.PORTFOLIO_FILE.write_text(json.dumps({"SBER": {"qty": None, "entry": None}}), encoding="utf-8")
        self.assertEqual(load_portfolio(), {})

    def test_roundtrip_and_trade_log(self):
        save_portfolio({"NVTK": Position("NVTK", -5, 1124, -104), "GMKN": Position("GMKN", 100, 156)})
        loaded = load_portfolio()
        self.assertEqual(loaded["NVTK"].qty, -5)
        self.assertAlmostEqual(loaded["NVTK"].realized, -104)
        record_trade("NVTK", "sell", 13, 1124, -104)
        trades = json.loads(config.TRADES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(trades[0]["ticker"], "NVTK")

    def test_lines_with_short_and_missing_price(self):
        positions = {"NVTK": Position("NVTK", -5, 1124, -104), "GMKN": Position("GMKN", 100, 156),
                     "OZPH": Position("OZPH", 100, 50)}
        text = "\n".join(portfolio_lines(positions, {"NVTK": 1151.7, "GMKN": 114.34}))
        self.assertIn("NVTK | -5 (шорт) | 1124.00 | 1151.70 | -138.50 ₽", text)
        self.assertIn("GMKN | 100 (лонг) | 156.00 | 114.34 | -4166.00 ₽", text)
        self.assertIn("Зафиксировано по закрытым частям: -104.00 ₽", text)
        self.assertIn("Риск шорта NVTK", text)
        self.assertIn("Нет цены для: OZPH", text)


if __name__ == "__main__":
    unittest.main()
