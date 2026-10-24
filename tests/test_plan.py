import unittest

from helpers import TempDataCase

import config
from plan import load_plan, plan_lines
from portfolio import Position


class PlanTest(TempDataCase):
    def test_breach_for_long_and_short(self):
        (config.DATA_DIR / "plan.json").write_text(
            '{"nvtk": {"action": "закрыть шорт", "exit": 1188}, "GMKN": {"action": "держать", "exit": 112}}',
            encoding="utf-8")
        plan = load_plan()
        positions = {"NVTK": Position("NVTK", -5, 1124), "GMKN": Position("GMKN", 100, 156)}
        text = "\n".join(plan_lines(plan, positions, {"NVTK": 1190.0, "GMKN": 114.3}))
        self.assertIn("NVTK | закрыть шорт | 1188 | 1190.00 | -0.2% | ⚠️ УРОВЕНЬ ПРОЙДЕН", text)
        self.assertIn("GMKN | держать | 112 | 114.30 | -2.0% | ок", text)


    def test_set_and_remove(self):
        from plan import remove_plan_item, set_plan_item

        set_plan_item("sber", 280, "держать", "день инвестора")
        set_plan_item("SBER", 285, "держать, докупить на просадке")
        self.assertEqual(load_plan()["SBER"], {"action": "держать, докупить на просадке", "exit": 285,
                                               "note": "день инвестора"})
        self.assertTrue(remove_plan_item("SBER"))
        self.assertFalse(remove_plan_item("SBER"))
        self.assertEqual(load_plan(), {})


if __name__ == "__main__":
    unittest.main()
