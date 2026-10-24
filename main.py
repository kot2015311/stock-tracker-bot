"""Телеграм-бот для наблюдения за акциями Мосбиржи. Запуск: python main.py

Фоновые задачи: проверка цен и уровней, сбор новостей из каналов и лент,
отчёты по расписанию. Настройки — в файле .env (образец: .env.example).
"""

from __future__ import annotations

import asyncio
import logging
import sys

import config

config.load_env()
CA_BUNDLE = config.setup_ca_bundle()  # до импорта SDK Т-Инвестиций: gRPC читает сертификаты при старте

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from handlers import BOT_COMMANDS, App, build_router, load_watch, make_and_send_report, save_watch  # noqa: E402
from news_store import NewsStore  # noqa: E402
import users  # noqa: E402
from scheduler import news_loop, report_loop, watch_loop  # noqa: E402
from tinkoff_client import TinkoffMarket  # noqa: E402

log = logging.getLogger("stock-bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)  # не засорять лог каждым сообщением
    settings = config.load_settings()
    config.owner_id = settings.owner_id

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    app = App(settings=settings, market=TinkoffMarket(settings.tinkoff_token), store=NewsStore(), ca_bundle=CA_BUNDLE)

    dp = Dispatcher()
    dp.include_router(build_router(app))

    async def scheduled_report(_target) -> None:
        for uid in users.all_users():  # у каждого пользователя свой отчёт
            token = config.current_user.set(uid)
            try:
                await make_and_send_report(bot, app, uid, "Автоотчёт по расписанию", scheduled=True)
            except Exception:  # noqa: BLE001 - сбой у одного не мешает остальным
                log.exception("Ошибка автоотчёта (пользователь %s)", uid)
            finally:
                config.current_user.reset(token)

    tasks = [
        asyncio.create_task(watch_loop(bot, app.market, users.all_users, settings.check_interval, load_watch, save_watch)),
        asyncio.create_task(news_loop(app.store, settings.news_interval)),
    ]
    if settings.report_times:
        tasks.append(asyncio.create_task(report_loop(settings.report_times, settings.weekdays_only, scheduled_report)))

    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:  # noqa: BLE001 - меню команд не обязательно для работы
        log.warning("Не удалось обновить меню команд бота")

    times = ", ".join(t.strftime("%H:%M") for t in settings.report_times) or "выключены"
    log.info("Бот запущен. Цены: каждые %s сек. Новости: каждые %s сек. Отчёты: %s%s. Сертификаты: %s",
             settings.check_interval, settings.news_interval, times,
             " (по будням)" if settings.report_times and settings.weekdays_only else "",
             CA_BUNDLE or "системные")
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # русский текст и эмодзи в консоли Windows
        except (AttributeError, ValueError):
            pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
