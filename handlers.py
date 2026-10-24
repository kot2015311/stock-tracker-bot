"""Кнопки и команды бота в Telegram. Бот отвечает только владельцу (OWNER_ID из .env).

Всё, что вы пересылаете боту (посты каналов, ссылки, текст, файлы .txt/.html), сохраняется
как «ваши материалы» и попадает в следующий отчёт.
"""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

import alerts as alerts_mod
import users as users_mod
import config
from articles import clean_text, extract_article, fetch_article, find_urls, title_of
from check_sources import format_results, run_checks
from http_client import describe_error, open_http
from news import load_keywords, select_news
from plan import load_plan, plan_lines, remove_plan_item, set_plan_item
from portfolio import Position, apply_trade, fmt_qty, load_portfolio, portfolio_lines, record_trade, save_portfolio
from report import build_report, last_report_time, mark_report_sent, save_report_files
from storage import read_json, write_json
from utils import chunks, parse_number

log = logging.getLogger("stock-bot")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # без карточек-превью у ссылок

BOT_COMMANDS = [
    BotCommand(command="menu", description="Меню с кнопками"),
    BotCommand(command="report", description="Отчёт сейчас"),
    BotCommand(command="portfolio", description="Портфель и результат"),
    BotCommand(command="buy", description="Покупка: /buy SBER 10 291.5"),
    BotCommand(command="sell", description="Продажа или шорт: /sell NVTK 13 1124"),
    BotCommand(command="pos", description="Задать позицию: /pos NVTK -5 1124"),
    BotCommand(command="alert", description="Уведомить об уровне: /alert NVTK 1170"),
    BotCommand(command="alerts", description="Список уровней"),
    BotCommand(command="plan", description="План по позициям"),
    BotCommand(command="setplan", description="Изменить план: /setplan NVTK 1188 закрыть шорт"),
    BotCommand(command="delplan", description="Убрать бумагу из плана: /delplan NVTK"),
    BotCommand(command="news", description="Новости по бумагам: /news или /news SBER"),
    BotCommand(command="inbox", description="Присланные вами материалы"),
    BotCommand(command="check", description="Проверить источники"),
    BotCommand(command="help", description="Что умеет бот"),
]

HELP_TEXT = """<b>Что умеет бот</b>

Главное меню — кнопки внизу экрана, под строкой ввода. Если они пропали, отправьте /menu.

📄 <b>Отчёт</b> приходит сам по расписанию (REPORT_TIMES в .env) и по кнопке. В нём портфель, рынок, цены с индикаторами, дивиденды, новости по бумагам, обзоры брокеров и ваши материалы.

📰 <b>Новости</b> бот собирает сам из Telegram-каналов и RSS-лент (список в config/sources.json). Копировать страницы больше не нужно.

📥 <b>Свои материалы</b>: перешлите боту пост из любого канала, пришлите ссылку на статью, текст или файл .txt/.html. Бот уберёт мусор (меню, комментарии, формы) и добавит материал в следующий отчёт.

💼 <b>Портфель</b>
/buy SBER 10 291.5 — покупка (или закрытие шорта)
/sell NVTK 13 1124 — продажа; если продать больше, чем есть, остаток станет шортом
/pos NVTK -5 1124 — задать позицию вручную (минус — шорт, 0 — удалить)
Цену можно не писать: возьмётся текущая.

🔔 <b>Уровни</b>
/alert NVTK 1170 — сообщение, когда цена дойдёт до 1170
/alerts — список; /unalert NVTK — удалить уровни по бумаге

📌 <b>План</b>
/plan — что делать с каждой бумагой и уровень выхода
/setplan GMKN 112 держать, не усреднять — задать план (уровень можно заменить на «-»)
/delplan GMKN — убрать бумагу из плана
Когда цена пробьёт уровень выхода (лонг — вниз, шорт — вверх), бот пришлёт сообщение.

🩺 /check — проверить, какие источники доступны из вашей сети.

Бот показывает данные и факты. Решения о сделках принимаете вы."""


class Action(StatesGroup):
    price = State()
    add_ticker = State()
    add_threshold = State()


@dataclass
class App:
    """Всё, что нужно обработчикам: настройки, биржа, хранилище новостей."""

    settings: object
    market: object
    store: object
    ca_bundle: str | None = None
    report_locks: dict = field(default_factory=dict)  # отдельная блокировка на каждого пользователя

    def report_lock(self) -> asyncio.Lock:
        uid = config.current_user.get()
        if uid not in self.report_locks:
            self.report_locks[uid] = asyncio.Lock()
        return self.report_locks[uid]


# ---------------------------------------------------------------- список наблюдения
def load_watch() -> dict:
    data = read_json(config.WATCH_FILE, {})
    return data if isinstance(data, dict) else {}


def save_watch(watch: dict) -> None:
    write_json(config.WATCH_FILE, watch)


async def ensure_watched(app: App, ticker: str) -> tuple[dict | None, float | None, bool]:
    """Бумага из списка наблюдения (или находит и добавляет её). Возвращает (запись, цена, добавлена ли)."""
    watch = load_watch()
    item = watch.get(ticker)
    added = False
    if item is None:
        share = await app.market.find_share(ticker)
        if share is None:
            return None, None, False
        item = {"figi": share["figi"], "name": share["name"], "ref": None, "threshold": 3.0}
        added = True
    price = (await app.market.prices([item["figi"]])).get(item["figi"])
    if added:
        item["ref"] = price
        watch = load_watch()
        watch[ticker] = item
        save_watch(watch)
    return item, price, added


# ---------------------------------------------------------------- клавиатуры и отправка
# Кнопки постоянного меню под строкой ввода: текст кнопки -> действие
BUTTONS = {
    "📄 Отчёт": "report", "💼 Портфель": "portfolio", "📌 План": "plan",
    "📰 Новости": "news", "🔔 Уровни": "alerts", "📥 Материалы": "inbox",
    "📋 Список": "list", "💰 Цена": "price", "➕ Добавить": "add",
    "❌ Удалить": "delmenu", "🩺 Проверка": "check", "❓ Помощь": "help",
}


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянное меню внизу экрана, под строкой ввода."""
    names = list(BUTTONS)
    rows = [[KeyboardButton(text=n) for n in names[i:i + 3]] for i in range(0, len(names), 3)]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder="Кнопка, команда, ссылка или пересланный пост")


def back_kb() -> ReplyKeyboardMarkup:
    return main_menu_kb()


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")]])


def threshold_kb() -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text=f"{p}%", callback_data=f"thr:{p}") for p in (2, 3, 5, 10)]
    return InlineKeyboardMarkup(inline_keyboard=[row, [InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")]])


async def send_pre(message: Message, title: str, body: str, kb=None) -> None:
    """Отправляет таблицу моноширинным шрифтом, разбивая на несколько сообщений при необходимости."""
    parts = chunks(body)
    for idx, part in enumerate(parts):
        head = f"<b>{html.escape(title)}</b>\n" if idx == 0 and title else ""
        last = idx == len(parts) - 1
        await message.answer(f"{head}<pre>{html.escape(part)}</pre>", reply_markup=kb if last else None)


# ---------------------------------------------------------------- отчёт (общий для кнопки и расписания)
async def make_and_send_report(bot: Bot, app: App, chat_id: int, caption: str, scheduled: bool = False) -> bool:
    """Собирает отчёт и отправляет файлом. False, если отчёт уже собирается."""
    lock = app.report_lock()
    if lock.locked():
        return False
    async with lock:
        watch = load_watch()
        if not watch:
            if not scheduled:  # по расписанию пустой отчёт не шлём, чтобы не спамить
                await bot.send_message(chat_id, "Список наблюдения пуст: добавьте бумаги кнопкой «➕ Добавить».")
            return True
        report = await build_report(app.market, watch, store=app.store, since=last_report_time())
        await bot.send_document(
            chat_id, BufferedInputFile(report.text.encode("utf-8"), filename=report.filename), caption=caption,
        )
        try:
            save_report_files(report)
        except OSError:
            log.exception("Не удалось сохранить копию отчёта")
        mark_report_sent(report)
        if report.data.get("errors"):
            await bot.send_message(chat_id, f"⚠️ Часть источников не ответила ({len(report.data['errors'])}). "
                                            "Подробности в конце отчёта, проверка: /check")
        return True


# ---------------------------------------------------------------- роутер
def build_router(app: App) -> Router:
    router = Router()
    owner = app.settings.owner_id

    async def access(handler, event, data):
        """Пускает только владельца и тех, кому он открыл доступ, и выбирает папку с данными пользователя."""
        user = getattr(event, "from_user", None)
        if user is None:
            return None
        if not users_mod.is_allowed(user.id):
            if isinstance(event, Message) and (event.text or "").startswith("/start"):
                await event.answer(f"Бот частный. Ваш Telegram ID: <code>{user.id}</code>. "
                                   "Отправьте его владельцу бота, чтобы он открыл доступ.")
            return None
        token = config.current_user.set(user.id)
        try:
            return await handler(event, data)
        finally:
            config.current_user.reset(token)

    router.message.outer_middleware(access)
    router.callback_query.outer_middleware(access)

    # --- пользователи (только владелец)
    @router.message(Command("adduser", "deluser", "users"))
    async def cmd_users(message: Message, command: CommandObject) -> None:
        if message.from_user.id != owner:
            await message.answer("Эта команда только для владельца бота.")
            return
        arg = (command.args or "").strip()
        if command.command == "users":
            others = users_mod.load_users()
            text = "Пользователи: " + (", ".join(f"<code>{u}</code>" for u in others) if others else "только вы")
            await message.answer(text + "\nДобавить: /adduser ID, удалить: /deluser ID")
            return
        if not arg.lstrip("-").isdigit():
            await message.answer(f"Формат: <code>/{command.command} 123456789</code> (ID человека пришлёт бот на /start)")
            return
        uid = int(arg)
        if command.command == "adduser":
            done = users_mod.add_user(uid)
            await message.answer("✅ Доступ открыт. Пусть отправит боту /start." if done else "Уже есть доступ.")
        else:
            done = users_mod.remove_user(uid)
            await message.answer("Доступ закрыт. Его данные остались в data/users/." if done else "Такого пользователя нет.")

    # --- меню и помощь
    @router.message(Command("start", "menu"))
    async def cmd_menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb())

    @router.message(F.text.in_(set(BUTTONS)))
    async def on_button(message: Message, state: FSMContext, bot: Bot) -> None:
        """Нажатие кнопки постоянного меню. Работает в любом состоянии диалога."""
        await state.clear()
        action = BUTTONS[message.text]
        if action == "report":
            await run_report(message, bot)
        elif action == "portfolio":
            await show_portfolio(message)
        elif action == "plan":
            await show_plan(message)
        elif action == "news":
            await show_news(message, None)
        elif action == "alerts":
            await show_alerts(message)
        elif action == "inbox":
            await show_inbox(message)
        elif action == "list":
            await do_list(message)
        elif action == "price":
            await do_price(message, state)
        elif action == "add":
            await do_add(message, state)
        elif action == "delmenu":
            await do_delmenu(message)
        elif action == "check":
            await run_check(message)
        else:
            await message.answer(HELP_TEXT, reply_markup=back_kb())

    @router.callback_query(F.data.in_({"menu", "cancel"}))
    async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer()
        await callback.message.answer("Выберите действие:", reply_markup=main_menu_kb())

    @router.message(Command("help"))
    async def cmd_help(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(HELP_TEXT, reply_markup=back_kb())

    @router.callback_query(F.data == "help")
    async def cb_help(callback: CallbackQuery) -> None:
        await callback.answer()
        await callback.message.answer(HELP_TEXT, reply_markup=back_kb())

    # --- отчёт
    async def run_report(message: Message, bot: Bot) -> None:
        await message.answer("Собираю отчёт, это до минуты…")
        try:
            started = await make_and_send_report(bot, app, message.chat.id, "Отчёт по запросу")
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка сборки отчёта")
            await message.answer(f"Не удалось собрать отчёт: {html.escape(describe_error(exc))}", reply_markup=back_kb())
            return
        if not started:
            await message.answer("Отчёт уже собирается, он придёт через минуту.")

    @router.message(Command("report"))
    async def cmd_report(message: Message, bot: Bot, state: FSMContext) -> None:
        await state.clear()
        await run_report(message, bot)

    @router.callback_query(F.data == "report")
    async def cb_report(callback: CallbackQuery, bot: Bot) -> None:
        await callback.answer("Собираю отчёт…")
        await run_report(callback.message, bot)

    # --- портфель
    async def show_portfolio(message: Message) -> None:
        positions = load_portfolio()
        watch = load_watch()
        figis = {t: watch[t]["figi"] for t in positions if t in watch and watch[t].get("figi")}
        try:
            by_figi = await app.market.prices(list(figis.values()))
        except Exception as exc:  # noqa: BLE001
            by_figi = {}
            await message.answer(f"Цены не получены: {html.escape(describe_error(exc))}")
        prices = {t: by_figi.get(f) for t, f in figis.items()}
        await send_pre(message, "💼 Портфель", "\n".join(portfolio_lines(positions, prices)), back_kb())

    @router.message(Command("portfolio", "pf"))
    async def cmd_portfolio(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show_portfolio(message)

    @router.callback_query(F.data == "portfolio")
    async def cb_portfolio(callback: CallbackQuery) -> None:
        await callback.answer()
        await show_portfolio(callback.message)

    async def trade(message: Message, command: CommandObject, side: str) -> None:
        example = "/buy SBER 10 291.5" if side == "buy" else "/sell NVTK 13 1124"
        args = (command.args or "").split()
        if len(args) not in (2, 3):
            await message.answer(f"Формат: <code>{example}</code> (тикер, количество, цена; цену можно не писать)")
            return
        ticker = args[0].upper()
        try:
            qty = parse_number(args[1])
            price = parse_number(args[2]) if len(args) == 3 else None
        except ValueError:
            await message.answer(f"Не понял числа. Пример: <code>{example}</code>")
            return
        try:
            item, current, added = await ensure_watched(app, ticker)
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"Ошибка связи с биржей: {html.escape(describe_error(exc))}")
            return
        if item is None:
            await message.answer(f"Акция {html.escape(ticker)} не найдена на Мосбирже.")
            return
        price = price or current
        if not price:
            await message.answer("Не удалось получить текущую цену, укажите её в команде.")
            return
        positions = load_portfolio()
        pos = positions.get(ticker) or Position(ticker)
        try:
            realized = apply_trade(pos, side, qty, price)
        except ValueError as exc:
            await message.answer(f"Сделка не записана: {html.escape(str(exc))}")
            return
        positions[ticker] = pos
        save_portfolio(positions)
        record_trade(ticker, side, qty, price, realized)
        verb = "Покупка" if side == "buy" else "Продажа"
        text = f"✅ {verb} {fmt_qty(qty)} {html.escape(ticker)} по {price:.2f} записана."
        if realized:
            text += f"\nЗафиксировано: {realized:+.2f} ₽"
        if pos.qty:
            entry = f"{pos.entry:.2f}" if pos.entry is not None else "н/д"
            text += f"\nТеперь: {fmt_qty(pos.qty)} шт ({pos.side}), средняя цена входа {entry}."
        else:
            text += "\nПозиция закрыта."
        if added:
            text += f"\n{html.escape(ticker)} добавлена в список наблюдения."
        await message.answer(text, reply_markup=back_kb())

    @router.message(Command("buy"))
    async def cmd_buy(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        await trade(message, command, "buy")

    @router.message(Command("sell"))
    async def cmd_sell(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        await trade(message, command, "sell")

    @router.message(Command("pos"))
    async def cmd_pos(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        args = (command.args or "").split()
        if len(args) not in (2, 3):
            await message.answer("Формат: <code>/pos NVTK -5 1124</code> (минус — шорт, 0 — удалить позицию)")
            return
        ticker = args[0].upper()
        try:
            qty = parse_number(args[1])
            price = parse_number(args[2]) if len(args) == 3 else None
        except ValueError:
            await message.answer("Не понял числа. Пример: <code>/pos NVTK -5 1124</code>")
            return
        positions = load_portfolio()
        if qty == 0:
            positions.pop(ticker, None)
            save_portfolio(positions)
            await message.answer(f"Позиция {html.escape(ticker)} удалена.", reply_markup=back_kb())
            return
        try:
            item, current, added = await ensure_watched(app, ticker)
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"Ошибка связи с биржей: {html.escape(describe_error(exc))}")
            return
        if item is None:
            await message.answer(f"Акция {html.escape(ticker)} не найдена на Мосбирже.")
            return
        entry = price or current
        old = positions.get(ticker)
        positions[ticker] = Position(ticker, qty, entry, old.realized if old else 0.0)
        save_portfolio(positions)
        side = "шорт" if qty < 0 else "лонг"
        note = "" if price else " (текущая цена, своей цены вы не указали)"
        text = f"✅ {html.escape(ticker)}: {fmt_qty(qty)} шт ({side}), вход {entry:.2f}{note}."
        if added:
            text += f"\n{html.escape(ticker)} добавлена в список наблюдения."
        await message.answer(text, reply_markup=back_kb())

    # --- уровни
    @router.message(Command("plan"))
    async def cmd_plan(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show_plan(message)

    async def show_plan(message: Message) -> None:
        plan = load_plan()
        if not plan:
            await message.answer("План пуст. Задайте его командой, например:\n"
                                 "<code>/setplan GMKN 112 держать, не усреднять</code>", reply_markup=back_kb())
            return
        positions = load_portfolio()
        watch = load_watch()
        figis = {t: watch[t]["figi"] for t in plan if t in watch and watch[t].get("figi")}
        try:
            by_figi = await app.market.prices(list(figis.values()))
        except Exception:  # noqa: BLE001
            by_figi = {}
        prices = {t: by_figi.get(f) for t, f in figis.items()}
        await send_pre(message, "📌 План по позициям", "\n".join(plan_lines(plan, positions, prices)), back_kb())

    @router.message(Command("setplan"))
    async def cmd_setplan(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        args = (command.args or "").split(maxsplit=2)
        if len(args) < 2:
            await message.answer(
                "Формат: <code>/setplan ТИКЕР УРОВЕНЬ что делать</code>\n"
                "Пример: <code>/setplan GMKN 112 держать, не усреднять</code>\n"
                "Без уровня выхода: <code>/setplan SBER - держать</code>\n"
                "Для лонга уровень срабатывает при падении ниже него, для шорта — при росте выше.")
            return
        ticker = args[0].upper()
        level = None
        if args[1] not in {"-", "—", "0"}:
            try:
                level = parse_number(args[1])
            except ValueError:
                await message.answer("Не понял уровень. Пример: <code>/setplan GMKN 112 держать</code>")
                return
        action = args[2].strip() if len(args) > 2 else ""
        try:
            await ensure_watched(app, ticker)  # без бумаги в списке бот не узнает цену для проверки уровня
        except Exception:  # noqa: BLE001 - план сохраним и без цены
            pass
        item = set_plan_item(ticker, level, action)
        level_text = f", выход {item['exit']:g}" if item.get("exit") else ", без уровня выхода"
        await message.answer(f"📌 {html.escape(ticker)}: {html.escape(item['action'])}{level_text}.",
                             reply_markup=back_kb())

    @router.message(Command("delplan"))
    async def cmd_delplan(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        ticker = (command.args or "").strip().upper()
        if not ticker:
            await message.answer("Формат: <code>/delplan GMKN</code>")
            return
        done = remove_plan_item(ticker)
        await message.answer(f"{html.escape(ticker)} убрана из плана." if done else f"{html.escape(ticker)} в плане нет.",
                             reply_markup=back_kb())

    async def show_alerts(message: Message) -> None:
        current = alerts_mod.load_alerts()
        if not current:
            await message.answer("Уровней нет. Пример: <code>/alert NVTK 1170</code>", reply_markup=back_kb())
            return
        rows = [[InlineKeyboardButton(text=f"🗑 {a.describe()}", callback_data=f"unalert:{i}")]
                for i, a in enumerate(current)]
        rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu")])
        lines = [f"• {html.escape(a.describe())} (поставлен {html.escape(a.created)})" for a in current]
        await message.answer("<b>🔔 Уровни</b>\n" + "\n".join(lines) + "\n\nНажмите, чтобы удалить:",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.message(Command("alerts"))
    async def cmd_alerts(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show_alerts(message)

    @router.callback_query(F.data == "alerts")
    async def cb_alerts(callback: CallbackQuery) -> None:
        await callback.answer()
        await show_alerts(callback.message)

    @router.callback_query(F.data.startswith("unalert:"))
    async def cb_unalert(callback: CallbackQuery) -> None:
        current = alerts_mod.load_alerts()
        try:
            alert = current[int(callback.data.split(":", 1)[1])]
        except (ValueError, IndexError):
            await callback.answer("Уровень уже удалён")
            return
        alerts_mod.remove_alerts([alert])
        await callback.answer("Удалено")
        await show_alerts(callback.message)

    @router.message(Command("alert"))
    async def cmd_alert(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        args = (command.args or "").split()
        if len(args) != 2:
            await message.answer("Формат: <code>/alert NVTK 1170</code> — сообщу, когда цена дойдёт до уровня")
            return
        ticker = args[0].upper()
        try:
            level = parse_number(args[1])
        except ValueError:
            await message.answer("Не понял уровень. Пример: <code>/alert NVTK 1170</code>")
            return
        try:
            item, price, added = await ensure_watched(app, ticker)
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"Ошибка связи с биржей: {html.escape(describe_error(exc))}")
            return
        if item is None or not price:
            await message.answer(f"Не нашёл {html.escape(ticker)} или её цену.")
            return
        alert = alerts_mod.make_alert(ticker, item["figi"], level, price)
        current = alerts_mod.load_alerts()
        current.append(alert)
        alerts_mod.save_alerts(current)
        direction = "поднимется" if alert.direction == "up" else "опустится"
        await message.answer(
            f"🔔 Сообщу, когда {html.escape(ticker)} {direction} до {level:g} (сейчас {price:.2f}, "
            f"до уровня {(level - price) / price * 100:+.1f}%). Проверка раз в {app.settings.check_interval // 60} мин.",
            reply_markup=back_kb(),
        )

    @router.message(Command("unalert"))
    async def cmd_unalert(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        args = (command.args or "").split()
        if not args:
            await message.answer("Формат: <code>/unalert NVTK</code> или <code>/unalert NVTK 1170</code>")
            return
        ticker = args[0].upper()
        level = None
        if len(args) > 1:
            try:
                level = parse_number(args[1])
            except ValueError:
                level = None
        current = alerts_mod.load_alerts()
        to_remove = [a for a in current if a.ticker == ticker and (level is None or abs(a.level - level) < 1e-9)]
        alerts_mod.remove_alerts(to_remove)
        await message.answer(f"Удалено уровней: {len(to_remove)}.", reply_markup=back_kb())

    # --- новости и материалы
    async def show_news(message: Message, only: str | None) -> None:
        watch = load_watch()
        if only:
            watch = {t: v for t, v in watch.items() if t == only}
            if not watch:
                await message.answer(f"{html.escape(only)} нет в списке наблюдения.", reply_markup=back_kb())
                return
        keywords, macro = load_keywords(watch)
        selection = select_news(app.store.visible(), keywords, macro, datetime.now(config.MSK), last_report_time())
        if not selection.by_ticker:
            note = "" if app.store.all() else " База новостей пока пуста: первый сбор идёт после запуска бота."
            await message.answer("Упоминаний ваших бумаг за 3 дня не найдено." + note, reply_markup=back_kb())
            return
        lines = []
        for ticker, mentions in selection.by_ticker.items():
            lines.append(f"<b>{html.escape(ticker)}</b>")
            for m in mentions:
                flag = "🆕 " if m.is_new else ""
                link = f' <a href="{html.escape(m.item.link)}">→</a>' if m.item.link else ""
                lines.append(f"{flag}{m.item.date_text[5:]} [{html.escape(m.item.source)}] "
                             f"{html.escape(m.excerpt[:300])}{link}")
            lines.append("")
        parts = chunks("\n".join(lines))
        for idx, part in enumerate(parts):
            await message.answer(part, reply_markup=back_kb() if idx == len(parts) - 1 else None,
                                 link_preview_options=NO_PREVIEW)

    @router.message(Command("news"))
    async def cmd_news(message: Message, command: CommandObject, state: FSMContext) -> None:
        await state.clear()
        await show_news(message, (command.args or "").strip().upper() or None)

    @router.callback_query(F.data == "news")
    async def cb_news(callback: CallbackQuery) -> None:
        await callback.answer()
        await show_news(callback.message, None)

    async def show_inbox(message: Message) -> None:
        items = app.store.manual()[:15]
        if not items:
            await message.answer("Материалов пока нет. Перешлите боту пост, ссылку или текст.", reply_markup=back_kb())
            return
        lines = ["<b>📥 Ваши материалы</b> (последние 15, хранятся 14 дней)"]
        for item in items:
            when = item.added.strftime("%d.%m %H:%M") if item.added else ""
            lines.append(f"• {when} {html.escape(item.title[:90])} ({len(item.text)} симв.)")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить все", callback_data="inbox_clear")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="menu")],
        ])
        await message.answer("\n".join(lines), reply_markup=kb)

    @router.message(Command("inbox"))
    async def cmd_inbox(message: Message, state: FSMContext) -> None:
        await state.clear()
        await show_inbox(message)

    @router.callback_query(F.data == "inbox")
    async def cb_inbox(callback: CallbackQuery) -> None:
        await callback.answer()
        await show_inbox(callback.message)

    @router.callback_query(F.data == "inbox_clear")
    async def cb_inbox_clear(callback: CallbackQuery) -> None:
        removed = app.store.clear_manual()
        await callback.answer(f"Удалено: {removed}")
        await callback.message.answer(f"Удалено материалов: {removed}.", reply_markup=back_kb())

    # --- проверка источников
    async def run_check(message: Message) -> None:
        await message.answer("Проверяю источники из вашей сети, это до минуты…")
        try:
            async with open_http() as http:
                results = await run_checks(http, market=app.market, bot_token=app.settings.bot_token)
        except Exception as exc:  # noqa: BLE001
            await message.answer(f"Проверка не удалась: {html.escape(describe_error(exc))}")
            return
        text = format_results(results, app.ca_bundle)
        parts = chunks(text)
        for idx, part in enumerate(parts):
            await message.answer(html.escape(part), reply_markup=back_kb() if idx == len(parts) - 1 else None)

    @router.message(Command("check"))
    async def cmd_check(message: Message, state: FSMContext) -> None:
        await state.clear()
        await run_check(message)

    @router.callback_query(F.data == "check")
    async def cb_check(callback: CallbackQuery) -> None:
        await callback.answer()
        await run_check(callback.message)

    # --- список наблюдения
    @router.callback_query(F.data == "list")
    async def cb_list(callback: CallbackQuery) -> None:
        await callback.answer()
        await do_list(callback.message)

    async def do_list(message: Message) -> None:
        watch = load_watch()
        if not watch:
            text = "Список пуст. Нажмите «Добавить»."
        else:
            lines = [f"• {html.escape(t)} — {html.escape(v.get('name', ''))}: опора {v['ref']:.2f} ₽, порог {v['threshold']}%"
                     if v.get("ref") else f"• {html.escape(t)}" for t, v in watch.items()]
            text = "<b>Отслеживаю</b> (уведомлю, если цена уйдёт от опоры на порог):\n" + "\n".join(lines)
        await message.answer(text, reply_markup=back_kb())

    @router.callback_query(F.data == "price")
    async def cb_price(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await do_price(callback.message, state)

    async def do_price(message: Message, state: FSMContext) -> None:
        await state.set_state(Action.price)
        await message.answer("Напишите тикер, например SBER", reply_markup=cancel_kb())

    @router.message(Action.price, F.text)
    async def msg_price(message: Message, state: FSMContext) -> None:
        ticker = message.text.strip().upper()
        await state.clear()
        try:
            share = await app.market.find_share(ticker)
            if share is None:
                await message.answer(f"Акция {html.escape(ticker)} не найдена.", reply_markup=back_kb())
                return
            price = (await app.market.prices([share["figi"]])).get(share["figi"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка получения цены")
            await message.answer(f"Ошибка связи с биржей: {html.escape(describe_error(exc))}", reply_markup=back_kb())
            return
        if not price:
            await message.answer("Цена не получена. Попробуйте позже.", reply_markup=back_kb())
            return
        await message.answer(f"<b>{html.escape(ticker)}</b> ({html.escape(share['name'])}): {price:.2f} ₽",
                             reply_markup=back_kb())

    @router.callback_query(F.data == "add")
    async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await do_add(callback.message, state)

    async def do_add(message: Message, state: FSMContext) -> None:
        await state.set_state(Action.add_ticker)
        await message.answer("Напишите тикер акции, например TATNP", reply_markup=cancel_kb())

    @router.message(Action.add_ticker, F.text)
    async def msg_add_ticker(message: Message, state: FSMContext) -> None:
        ticker = message.text.strip().upper()
        try:
            share = await app.market.find_share(ticker)
            if share is None:
                await message.answer(f"Акция {html.escape(ticker)} не найдена. Попробуйте другой тикер.",
                                     reply_markup=cancel_kb())
                return
            price = (await app.market.prices([share["figi"]])).get(share["figi"])
        except Exception as exc:  # noqa: BLE001
            await state.clear()
            await message.answer(f"Ошибка связи с биржей: {html.escape(describe_error(exc))}", reply_markup=back_kb())
            return
        if not price:
            await state.clear()
            await message.answer("Биржа не вернула цену. Попробуйте позже.", reply_markup=back_kb())
            return
        await state.update_data(ticker=ticker, figi=share["figi"], name=share["name"], ref=price)
        await state.set_state(Action.add_threshold)
        await message.answer(
            f"<b>{html.escape(ticker)}</b> ({html.escape(share['name'])}), цена {price:.2f} ₽.\n"
            "Выберите порог уведомления о движении цены:",
            reply_markup=threshold_kb(),
        )

    @router.callback_query(Action.add_threshold, F.data.startswith("thr:"))
    async def cb_threshold(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        threshold = float(callback.data.split(":", 1)[1])
        watch = load_watch()
        watch[data["ticker"]] = {"figi": data["figi"], "name": data["name"], "ref": data["ref"], "threshold": threshold}
        save_watch(watch)
        await state.clear()
        await callback.answer("Добавлено")
        await callback.message.answer(
            f"✅ {html.escape(data['ticker'])} добавлена. Опорная цена {data['ref']:.2f} ₽, порог {threshold:g}%.\n"
            "Ключевые слова для новостей можно уточнить в config/keywords.json.",
            reply_markup=main_menu_kb(),
        )

    @router.callback_query(F.data == "delmenu")
    async def cb_delmenu(callback: CallbackQuery) -> None:
        await callback.answer()
        await do_delmenu(callback.message)

    async def do_delmenu(message: Message) -> None:
        watch = load_watch()
        if not watch:
            await message.answer("Список пуст.", reply_markup=back_kb())
            return
        rows = [[InlineKeyboardButton(text=f"❌ {t}", callback_data=f"del:{t}")] for t in watch]
        rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="menu")])
        await message.answer("Выберите акцию для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.callback_query(F.data.startswith("del:"))
    async def cb_delete(callback: CallbackQuery) -> None:
        ticker = callback.data.split(":", 1)[1]
        watch = load_watch()
        if ticker in watch:
            del watch[ticker]
            save_watch(watch)
            text = f"{html.escape(ticker)} удалена из списка."
            if ticker in load_portfolio():
                text += " Позиция в портфеле осталась, но без цены. Удалить её: /pos " + html.escape(ticker) + " 0"
        else:
            text = f"{html.escape(ticker)} нет в списке."
        await callback.answer()
        await callback.message.answer(text, reply_markup=main_menu_kb())

    # --- всё остальное: материалы для отчёта (должно быть последним)
    @router.message(StateFilter(None), F.text.startswith("/"))
    async def unknown_command(message: Message) -> None:
        await message.answer("Не знаю такой команды. Список: /help", reply_markup=back_kb())

    @router.message(StateFilter(None), F.document)
    async def msg_document(message: Message, bot: Bot) -> None:
        doc = message.document
        name = (doc.file_name or "").lower()
        if not name.endswith((".txt", ".md", ".html", ".htm")):
            await message.answer("Понимаю файлы .txt, .md и .html. Остальное пришлите текстом или ссылкой.")
            return
        if doc.file_size and doc.file_size > 5_000_000:
            await message.answer("Файл больше 5 МБ, пришлите его частями или ссылкой.")
            return
        raw = (await bot.download(doc)).read()
        if name.endswith((".html", ".htm")):
            try:
                article = extract_article(raw, "")
                title, text = article.title, article.text
            except ValueError as exc:
                await message.answer(f"Не получилось достать текст: {html.escape(str(exc))}")
                return
        else:
            try:
                decoded = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                decoded = raw.decode("cp1251", errors="replace")
            text = clean_text(decoded)
            title = title_of(text)
        item = app.store.add_manual(title, text, source=f"Файл {doc.file_name}")
        await message.answer(f"✅ Сохранил «{html.escape(item.title[:80])}» ({len(text)} симв.). Попадёт в следующий отчёт.")

    @router.message(StateFilter(None), F.text | F.caption)
    async def msg_material(message: Message) -> None:
        raw = message.text or message.caption or ""
        urls = find_urls(raw)
        for entity in (message.entities or message.caption_entities or []):
            if entity.type == "text_link" and entity.url and entity.url not in urls:
                urls.append(entity.url)

        origin = message.forward_origin
        source, link, published = "Вы прислали", "", None
        if origin is not None:
            published = getattr(origin, "date", None)
            if isinstance(published, datetime):
                published = published.astimezone(config.MSK) if published.tzinfo else published.replace(tzinfo=config.MSK)
            chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
            if chat is not None:
                source = f"Переслано из «{chat.title}»" if chat.title else "Переслано"
                if chat.username and getattr(origin, "message_id", None):
                    link = f"https://t.me/{chat.username}/{origin.message_id}"
            else:
                source = "Переслано"

        text_without_urls = raw
        for url in urls:
            text_without_urls = text_without_urls.replace(url, "")
        if urls and len(text_without_urls.strip()) < 40 and origin is None:
            # Прислали только ссылки: скачиваем статьи
            await message.answer("Читаю ссылку…" if len(urls) == 1 else f"Читаю ссылки ({min(len(urls), 3)})…")
            async with open_http() as http:
                for url in urls[:3]:
                    try:
                        article = await fetch_article(http, url)
                    except Exception as exc:  # noqa: BLE001
                        app.store.add_manual(title_of(raw), raw, link=url, source="Ссылка (текст не получен)")
                        await message.answer(
                            f"⚠️ {html.escape(url[:80])}\nНе смог прочитать: {html.escape(describe_error(exc))}.\n"
                            "Ссылка сохранена. Можно скопировать текст статьи и прислать его сюда.",
                            link_preview_options=NO_PREVIEW,
                        )
                        continue
                    app.store.add_manual(article.title, article.text, link=url, source="Статья по ссылке")
                    await message.answer(
                        f"✅ Сохранил статью «{html.escape(article.title[:80])}» ({len(article.text)} симв.). "
                        "Попадёт в следующий отчёт.",
                        link_preview_options=NO_PREVIEW,
                    )
            return

        text = clean_text(raw) if origin is None else raw.strip()
        if len(text) < 15:
            await message.answer("Слишком короткий текст. Команды: /help", reply_markup=back_kb())
            return
        item = app.store.add_manual(title_of(text), text, link=link, source=source, published=published)
        removed = len(raw) - len(text)
        extra = f" Убрал мусор: {removed} симв." if removed > 200 else ""
        await message.answer(f"✅ Сохранил «{html.escape(item.title[:80])}» ({len(text)} симв.).{extra} "
                             "Попадёт в следующий отчёт.")

    return router
