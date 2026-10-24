import asyncio
import unittest
from datetime import datetime, time, timedelta

from helpers import FakeMarket, TempDataCase

from alerts import load_alerts, make_alert, save_alerts
from config import MSK
from scheduler import check_prices_once, next_report_time, report_loop

TIMES = [time(10, 5), time(16, 5), time(19, 5)]


class NextRunTest(unittest.TestCase):
    def test_same_day(self):
        now = datetime(2026, 10, 23, 12, 0, tzinfo=MSK)  # пятница
        self.assertEqual(next_report_time(now, TIMES), datetime(2026, 10, 23, 16, 5, tzinfo=MSK))

    def test_after_last_time_on_friday_goes_to_monday(self):
        now = datetime(2026, 10, 23, 19, 6, tzinfo=MSK)
        self.assertEqual(next_report_time(now, TIMES), datetime(2026, 10, 26, 10, 5, tzinfo=MSK))
        self.assertEqual(next_report_time(now, TIMES, weekdays_only=False), datetime(2026, 10, 24, 10, 5, tzinfo=MSK))

    def test_exact_time_moves_to_next(self):
        now = datetime(2026, 10, 23, 16, 5, tzinfo=MSK)
        self.assertEqual(next_report_time(now, TIMES), datetime(2026, 10, 23, 19, 5, tzinfo=MSK))

    def test_disabled(self):
        self.assertIsNone(next_report_time(datetime.now(MSK), []))


class Clock:
    """Поддельные часы: sleep двигает время вперёд мгновенно."""

    def __init__(self, start: datetime, jump_after: datetime | None = None, jump_to: datetime | None = None):
        self.now = start
        self.jump_after, self.jump_to = jump_after, jump_to

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)
        if self.jump_after and self.now >= self.jump_after:
            self.now, self.jump_after = self.jump_to, None  # компьютер «проспал»


class LoopTest(unittest.TestCase):
    def run_loop(self, clock, runs_needed=2, limit=5000):
        runs = []

        async def run_report(target):
            runs.append(target)
            if len(runs) >= runs_needed:
                raise asyncio.CancelledError

        async def scenario():
            counter = {"n": 0}

            async def sleep(seconds):
                counter["n"] += 1
                if counter["n"] > limit:
                    raise asyncio.CancelledError
                await clock.sleep(seconds)

            try:
                await report_loop(TIMES, True, run_report, now_fn=clock, sleep=sleep)
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        return runs

    def test_runs_at_times(self):
        clock = Clock(datetime(2026, 10, 23, 9, 0, tzinfo=MSK))
        runs = self.run_loop(clock)
        self.assertEqual([r.strftime("%H:%M") for r in runs], ["10:05", "16:05"])

    def test_missed_report_after_sleep_is_skipped(self):
        start = datetime(2026, 10, 23, 9, 0, tzinfo=MSK)
        clock = Clock(start, jump_after=datetime(2026, 10, 23, 10, 0, tzinfo=MSK),
                      jump_to=datetime(2026, 10, 23, 12, 0, tzinfo=MSK))
        runs = self.run_loop(clock)
        self.assertEqual([r.strftime("%H:%M") for r in runs], ["16:05", "19:05"])

    def test_error_does_not_stop_loop(self):
        clock = Clock(datetime(2026, 10, 23, 9, 0, tzinfo=MSK))
        calls = []

        async def run_report(target):
            calls.append(target)
            if len(calls) == 1:
                raise RuntimeError("сбой")
            raise asyncio.CancelledError

        async def scenario():
            try:
                await report_loop(TIMES, True, run_report, now_fn=clock, sleep=clock.sleep)
            except asyncio.CancelledError:
                pass

        asyncio.run(scenario())
        self.assertEqual(len(calls), 2)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)


class PriceCheckTest(TempDataCase):
    def test_moves_and_levels(self):
        watch = {"NVTK": {"figi": "F1", "ref": 1100.0, "threshold": 3.0}}
        saved = {}
        save_alerts([make_alert("NVTK", "F1", 1150, 1124)])
        bot = FakeBot()
        asyncio.run(check_prices_once(bot, FakeMarket({"F1": 1151.7}), 1, lambda: dict(watch),
                                      lambda w: saved.update(w)))
        self.assertEqual(len(bot.sent), 2)
        self.assertIn("NVTK", bot.sent[0])
        self.assertIn("дошла до уровня 1150", bot.sent[1])
        self.assertEqual(saved["NVTK"]["ref"], 1151.7)
        self.assertEqual(load_alerts(), [])


class PlanLevelTest(TempDataCase):
    def test_breach_is_reported_once_and_rearmed(self):
        import json

        import config
        from portfolio import Position, save_portfolio

        (config.DATA_DIR / "plan.json").write_text(json.dumps({"NVTK": {"action": "закрыть шорт", "exit": 1188}}),
                                                   encoding="utf-8")
        save_portfolio({"NVTK": Position("NVTK", -5, 1124)})
        watch = {"NVTK": {"figi": "F1", "ref": 1190.0, "threshold": 50.0}}
        bot = FakeBot()
        run = lambda price: asyncio.run(check_prices_once(bot, FakeMarket({"F1": price}), 1, lambda: dict(watch),
                                                          lambda w: None))
        run(1185.0)
        self.assertEqual(bot.sent, [])
        run(1190.0)
        run(1195.0)  # повтор не нужен
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("выше уровня выхода 1188", bot.sent[0])
        run(1180.0)  # вернулась под уровень — напоминание снова взведено
        run(1189.0)
        self.assertEqual(len(bot.sent), 2)


if __name__ == "__main__":
    unittest.main()
