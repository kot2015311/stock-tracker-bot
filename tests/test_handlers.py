"""Проверка, что обработчики бота собираются. Нужен aiogram: без него тест пропускается."""

import asyncio
import importlib.util
import unittest

from helpers import FakeMarket, TempDataCase

HAS_AIOGRAM = importlib.util.find_spec("aiogram") is not None


@unittest.skipUnless(HAS_AIOGRAM, "aiogram не установлен")
class RouterTest(TempDataCase):
    def test_router_builds(self):
        from config import Settings
        from handlers import App, build_router
        from news_store import NewsStore

        async def scenario():
            app = App(settings=Settings("1:x", "t", 42), market=FakeMarket(), store=NewsStore())
            router = build_router(app)
            return len(router.message.handlers), len(router.callback_query.handlers)

        messages, callbacks = asyncio.run(scenario())
        self.assertGreaterEqual(messages, 15)
        self.assertGreaterEqual(callbacks, 15)


if __name__ == "__main__":
    unittest.main()
