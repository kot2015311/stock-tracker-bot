"""Уведомления о цене: движение на заданный процент и пересечение заданного уровня.

Уровень удобен для контроля риска: например, для шорта NVTK по 1124 можно поставить
/alert NVTK 1170 и получить сообщение, когда цена дойдёт до этой отметки.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import config
from storage import read_json, write_json


@dataclass
class LevelAlert:
    ticker: str
    figi: str
    level: float
    direction: str  # "up": сработать, когда цена поднимется до уровня; "down": когда опустится
    created: str = ""

    def triggered(self, price: float) -> bool:
        return price >= self.level if self.direction == "up" else price <= self.level

    def describe(self) -> str:
        arrow = "≥" if self.direction == "up" else "≤"
        return f"{self.ticker} {arrow} {self.level:g}"


def load_alerts(path: Path | None = None) -> list[LevelAlert]:
    data = read_json(path or config.ALERTS_FILE, [])
    result = []
    for raw in data if isinstance(data, list) else []:
        try:
            result.append(LevelAlert(
                ticker=str(raw["ticker"]).upper(),
                figi=str(raw["figi"]),
                level=float(raw["level"]),
                direction="up" if raw.get("direction") == "up" else "down",
                created=str(raw.get("created", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def save_alerts(alerts: list[LevelAlert], path: Path | None = None) -> None:
    write_json(path or config.ALERTS_FILE, [asdict(a) for a in alerts])


def remove_alerts(to_remove: list[LevelAlert], path: Path | None = None) -> None:
    """Удаляет уровни, перечитав файл: так не потеряются уровни, добавленные за время проверки."""
    keys = {(a.ticker, a.level, a.direction, a.created) for a in to_remove}
    current = load_alerts(path)
    save_alerts([a for a in current if (a.ticker, a.level, a.direction, a.created) not in keys], path)


def make_alert(ticker: str, figi: str, level: float, current_price: float, now: datetime | None = None) -> LevelAlert:
    """Направление выбирается само: уровень выше текущей цены — ждём роста, ниже — падения."""
    direction = "up" if level > current_price else "down"
    created = (now or datetime.now(config.MSK)).isoformat(timespec="minutes")
    return LevelAlert(ticker.upper(), figi, level, direction, created)


def check_levels(alerts: list[LevelAlert], prices: dict[str, float]) -> tuple[list[tuple[LevelAlert, float]], list[LevelAlert]]:
    """Возвращает (сработавшие уровни с ценой, оставшиеся). Сработавший уровень удаляется."""
    fired, remaining = [], []
    for alert in alerts:
        price = prices.get(alert.figi)
        if price and alert.triggered(price):
            fired.append((alert, price))
        else:
            remaining.append(alert)
    return fired, remaining


def check_moves(watch: dict, prices: dict[str, float]) -> tuple[list[tuple[str, float, float, float]], bool]:
    """Движение от опорной цены на порог из списка наблюдения.

    Возвращает список (тикер, цена, изменение %, старая опорная) и флаг, что опорные цены обновились.
    После уведомления опорная цена становится равной текущей.
    """
    moves = []
    changed = False
    for ticker, item in watch.items():
        price = prices.get(item.get("figi"))
        ref = item.get("ref")
        if not price or not ref:
            continue
        move = (price - ref) / ref * 100
        if abs(move) >= float(item.get("threshold", 3.0)):
            moves.append((ticker, price, move, ref))
            item["ref"] = price
            changed = True
    return moves, changed
