"""План по позициям (data/plan.json): что делать с бумагой и при какой цене выходить.

Формат: {"NVTK": {"action": "закрыть шорт", "exit": 1188, "note": "пробой максимума"}}.
Для лонга уровень выхода срабатывает, когда цена ниже него, для шорта — когда выше.
"""

from __future__ import annotations

import config
from storage import read_json, write_json


def load_plan() -> dict[str, dict]:
    data = read_json(config.PLAN_FILE, {})
    return {k.upper(): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def plan_lines(plan: dict[str, dict], positions: dict, prices: dict[str, float | None]) -> list[str]:
    """Таблица плана с проверкой: не пробит ли уровень выхода."""
    if not plan:
        return []
    lines = ["Тикер | План | Уровень выхода | Сейчас | До уровня | Статус"]
    for ticker, item in sorted(plan.items()):
        action = item.get("action", "")
        exit_level = item.get("exit")
        price = prices.get(ticker)
        pos = positions.get(ticker)
        is_short = bool(pos and pos.qty < 0)
        if exit_level and price:
            distance = (exit_level - price) / price * 100
            breached = is_breached(exit_level, price, is_short)
            status = "⚠️ УРОВЕНЬ ПРОЙДЕН — пересмотреть" if breached else "ок"
            lines.append(f"{ticker} | {action} | {exit_level:g} | {price:.2f} | {distance:+.1f}% | {status}")
        else:
            lines.append(f"{ticker} | {action} | {exit_level or '—'} | {price or 'н/д'} | | ")
        if item.get("note"):
            lines.append(f"    {item['note']}")
    return lines


def save_plan(plan: dict[str, dict]) -> None:
    write_json(config.PLAN_FILE, {k: plan[k] for k in sorted(plan)})


def set_plan_item(ticker: str, exit_level: float | None, action: str, note: str = "") -> dict:
    """Добавляет или меняет строку плана; заметку сохраняет, если новая не задана."""
    plan = load_plan()
    old = plan.get(ticker.upper(), {})
    item = {"action": action or old.get("action", "держать")}
    if exit_level:
        item["exit"] = exit_level
    if note or old.get("note"):
        item["note"] = note or old["note"]
    plan[ticker.upper()] = item
    save_plan(plan)
    return item


def remove_plan_item(ticker: str) -> bool:
    plan = load_plan()
    if plan.pop(ticker.upper(), None) is None:
        return False
    save_plan(plan)
    return True


def is_breached(level: float, price: float, is_short: bool) -> bool:
    return price > level if is_short else price < level


def plan_breaches(plan: dict[str, dict], positions: dict, prices: dict[str, float | None],
                  notified: dict[str, float]) -> tuple[list[tuple[str, dict, float, bool]], dict[str, float]]:
    """Какие уровни выхода пробиты впервые.

    notified — {тикер: уровень}, о котором уже сообщили. Сообщение приходит один раз на пробой;
    когда цена вернётся за уровень, напоминание снова «взводится».
    Возвращает [(тикер, строка плана, цена, шорт ли)] и новое состояние notified.
    """
    fired = []
    state = dict(notified)
    for ticker, item in plan.items():
        level, price = item.get("exit"), prices.get(ticker)
        if not level or not price:
            continue
        pos = positions.get(ticker)
        is_short = bool(pos and pos.qty < 0)
        if is_breached(level, price, is_short):
            if state.get(ticker) != level:
                fired.append((ticker, item, price, is_short))
                state[ticker] = level
        else:
            state.pop(ticker, None)
    return fired, state
