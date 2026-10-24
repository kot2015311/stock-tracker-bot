import unittest

from helpers import TempDataCase

from alerts import check_levels, check_moves, load_alerts, make_alert, remove_alerts, save_alerts


class AlertsTest(TempDataCase):
    def test_direction_is_chosen_from_current_price(self):
        self.assertEqual(make_alert("NVTK", "F1", 1170, 1151).direction, "up")
        self.assertEqual(make_alert("GMKN", "F2", 110, 114).direction, "down")

    def test_levels_fire_once(self):
        up = make_alert("NVTK", "F1", 1170, 1151)
        down = make_alert("GMKN", "F2", 110, 114)
        fired, remaining = check_levels([up, down], {"F1": 1171.0, "F2": 113.0})
        self.assertEqual([a.ticker for a, _ in fired], ["NVTK"])
        self.assertEqual([a.ticker for a in remaining], ["GMKN"])

    def test_remove_keeps_alerts_added_meanwhile(self):
        first = make_alert("NVTK", "F1", 1170, 1151)
        save_alerts([first])
        added_later = make_alert("SBER", "F3", 300, 291)
        save_alerts(load_alerts() + [added_later])
        remove_alerts([first])
        self.assertEqual([a.ticker for a in load_alerts()], ["SBER"])

    def test_moves_update_reference(self):
        watch = {"SBER": {"figi": "F", "ref": 100.0, "threshold": 3.0},
                 "LKOH": {"figi": "L", "ref": 100.0, "threshold": 3.0}}
        moves, changed = check_moves(watch, {"F": 104.0, "L": 101.0})
        self.assertTrue(changed)
        self.assertEqual([m[0] for m in moves], ["SBER"])
        self.assertEqual(watch["SBER"]["ref"], 104.0)
        self.assertEqual(watch["LKOH"]["ref"], 100.0)


if __name__ == "__main__":
    unittest.main()
