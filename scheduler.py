"""Фоновые задачи бота: проверка цен и уровней, сбор новостей, отчёты по расписанию.

Каждый цикл переживает любые разовые ошибки (они пишутся в лог) и не останавливает бота.
Ожидание идёт короткими шагами, поэтому после сна компьютера расписание не сбивается.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from html import escape
from typing import Awaitable, Callable

import alerts as alerts_mod
from config import MSK
from http_client import open_http

log = logging.getLogger("stock-bot")

MAX_LATE = timedelta(minutes=90)  # если компьютер проспал время отчёта дольше, этот отчёт пропускается
STEP = 30  # секунд: шаг ожидания


def next_report_time(now: datetime, times: list[time], weekdays_only: bool = True) -> datetime | None:
    """Ближайшее время отчёта строго позже now."""
    if not times:
        return None
    for day_shift in range(8):
        day = (now + timedelta(days=day_shift)).date()
        if weekdays_only and day.weekday() >= 5:
            continue
        for moment in times:
            candidate = datetime.combine(day, moment, tzinfo=MSK)
            if candidate > now:
                return candidate
    return None


async def wait_until(target: datetime, now_fn: Callable[[], datetime],
                     sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    while True:
        left = (target - now_fn()).total_seconds()
        if left <= 0:
            return
        await sleep(min(STEP, left))


async def report_loop(
    times: list[time],
    weekdays_only: bool,
    run_report: Callable[[datetime], Awaitable[None]],
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(MSK),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Отчёты в заданное время (по умолчанию 10:05, 16:05, 19:05 по будням)."""
    while True:
        target = next_report_time(now_fn(), times, weekdays_only)
        if target is None:
            return
        await wait_until(target, now_fn, sleep)
        late = now_fn() - target
        if late > MAX_LATE:
            log.warning("Отчёт на %s пропущен: компьютер спал (опоздание %s)", target, late)
            continue
        try:
            await run_report(target)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка автоотчёта")


async def news_loop(store, interval: int, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Сбор новостей из каналов и лент раз в interval секунд."""
    while True:
        try:
            async with open_http() as http:
                result = await store.collect(http)
            if result.new or result.errors:
                log.info("Новости: +%s, ошибок источников: %s", result.new, len(result.errors))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка сбора новостей")
        await sleep(interval)


async def check_prices_once(bot, market, owner_id: int, load_watch: Callable[[], dict],
                            save_watch: Callable[[dict], None]) -> None:
    """Одна проверка: движение на порог из списка и пересечение уровней из /alert."""
    watch = load_watch()
    level_alerts = alerts_mod.load_alerts()
    figis = [item["figi"] for item in watch.values() if item.get("figi")] + [a.figi for a in level_alerts]
    if not figis:
        return
    prices = await market.prices(list(dict.fromkeys(figis)))

    moves, changed = alerts_mod.check_moves(watch, prices)
    for ticker, price, move, ref in moves:
        arrow = "📈" if move > 0 else "📉"
        await bot.send_message(
            owner_id,
            f"{arrow} <b>{escape(ticker)}</b>: {price:.2f} ₽ ({move:+.2f}% к {ref:.2f} ₽)\n"
            "Это уведомление о движении цены, а не совет купить или продать.",
        )
    if changed:
        # Перечитываем список: пока шёл запрос цен, вы могли добавить или удалить бумагу
        current = load_watch()
        for ticker, price, _move, _ref in moves:
            if ticker in current:
                current[ticker]["ref"] = price
        save_watch(current)

    fired, remaining = alerts_mod.check_levels(level_alerts, prices)
    for alert, price in fired:
        await bot.send_message(
            owner_id,
            f"🔔 <b>{escape(alert.ticker)}</b> дошла до уровня {alert.level:g}: сейчас {price:.2f} ₽.\n"
            "Уровень удалён. Поставить снова: /alert " + f"{escape(alert.ticker)} {alert.level:g}",
        )
    if fired:
        alerts_mod.remove_alerts([alert for alert, _price in fired])

    await check_plan_levels(bot, owner_id, watch, prices)


async def check_plan_levels(bot, chat_id: int, watch: dict, prices_by_figi: dict) -> None:
    """Сообщение, когда цена пробила уровень выхода из плана (data/plan.json). Один раз на пробой."""
    import config
    from plan import load_plan, plan_breaches
    from portfolio import load_portfolio
    from storage import read_json, write_json

    plan = load_plan()
    if not plan:
        return
    prices = {t: prices_by_figi.get(item.get("figi")) for t, item in watch.items() if t in plan}
    state = read_json(config.STATE_FILE, {})
    notified = state.get("plan_notified", {}) if isinstance(state, dict) else {}
    fired, new_notified = plan_breaches(plan, load_portfolio(), prices, notified)
    for ticker, item, price, is_short in fired:
        side = "выше" if is_short else "ниже"
        what = "шорт" if is_short else "позиция"
        text = (f"⚠️ <b>{escape(ticker)}</b>: цена {price:.2f} ₽ {side} уровня выхода {item['exit']:g} из плана "
                f"({what}).\nПлан: {escape(item.get('action', ''))}")
        if item.get("note"):
            text += f"\n{escape(item['note'])}"
        text += "\nРешение за вами; изменить план: /setplan"
        await bot.send_message(chat_id, text)
    if new_notified != notified:
        state = read_json(config.STATE_FILE, {})  # перечитываем: отчёт мог записать сюда своё
        if not isinstance(state, dict):
            state = {}
        state["plan_notified"] = new_notified
        write_json(config.STATE_FILE, state)


async def watch_loop(bot, market, users: Callable[[], list[int]], interval: int, load_watch: Callable[[], dict],
                     save_watch: Callable[[dict], None]) -> None:
    """Проверка цен и уровней по очереди для каждого пользователя (у каждого свои файлы)."""
    import config

    while True:
        for uid in users():
            token = config.current_user.set(uid)
            try:
                await check_prices_once(bot, market, uid, load_watch, save_watch)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Ошибка при проверке цен (пользователь %s)", uid)
            finally:
                config.current_user.reset(token)
        await asyncio.sleep(interval)
