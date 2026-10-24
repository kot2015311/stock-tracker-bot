"""Позиции и сделки: средняя цена входа, зафиксированный и текущий результат, лонг и шорт.

Пример: было 8 NVTK по 1137, продали 13 по 1124. Восемь акций закрылись с результатом
(1124 − 1137) × 8 = −104 ₽, оставшиеся 5 стали шортом с ценой входа 1124.
Комиссии брокера и плата за перенос шорта не учитываются.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import config
from storage import read_json, write_json


@dataclass
class Position:
    ticker: str
    qty: float = 0.0  # больше нуля — лонг, меньше нуля — шорт
    entry: float | None = None  # средняя цена входа
    realized: float = 0.0  # зафиксированный результат по уже закрытым частям, ₽

    @property
    def side(self) -> str:
        return "шорт" if self.qty < 0 else "лонг"


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_portfolio(path: Path | None = None) -> dict[str, Position]:
    """Позиции из data/portfolio.json. Пустые строки-заготовки (qty: null) пропускаются."""
    data = read_json(path or config.PORTFOLIO_FILE, {})
    result: dict[str, Position] = {}
    if not isinstance(data, dict):
        return result
    for ticker, raw in data.items():
        if not isinstance(raw, dict):
            continue
        qty = _num(raw.get("qty")) or 0.0
        realized = _num(raw.get("realized")) or 0.0
        if not qty and not realized:
            continue
        result[ticker.upper()] = Position(ticker.upper(), qty, _num(raw.get("entry")), realized)
    return result


def save_portfolio(positions: dict[str, Position], path: Path | None = None) -> None:
    data = {}
    for ticker in sorted(positions):
        pos = positions[ticker]
        if not pos.qty and not pos.realized:
            continue
        item = asdict(pos)
        item.pop("ticker")
        item["realized"] = round(item["realized"], 2)
        data[ticker] = item
    write_json(path or config.PORTFOLIO_FILE, data)


def apply_trade(pos: Position, side: str, qty: float, price: float) -> float:
    """Учитывает сделку в позиции. side: 'buy' или 'sell'. Возвращает зафиксированный результат сделки, ₽."""
    if qty <= 0 or price <= 0:
        raise ValueError("количество и цена должны быть больше нуля")
    if side not in {"buy", "sell"}:
        raise ValueError("сделка бывает только buy или sell")
    signed = qty if side == "buy" else -qty
    old_qty, old_entry = pos.qty, pos.entry

    if old_qty == 0 or old_qty * signed > 0:
        # Открытие или наращивание позиции в ту же сторону: пересчитываем среднюю цену
        new_qty = old_qty + signed
        if old_qty and old_entry is None:
            pos.entry = None  # цена входа старой части неизвестна, среднюю посчитать нельзя
        else:
            base = old_entry if old_entry is not None else price
            pos.entry = (abs(old_qty) * base + qty * price) / abs(new_qty)
        pos.qty = new_qty
        return 0.0

    # Сделка против позиции: часть (или вся позиция) закрывается
    closed = min(qty, abs(old_qty))
    direction = 1 if old_qty > 0 else -1
    realized = (price - old_entry) * closed * direction if old_entry is not None else 0.0
    pos.realized += realized
    new_qty = old_qty + signed
    if abs(new_qty) < 1e-9:
        pos.qty, pos.entry = 0.0, None
    elif new_qty * old_qty > 0:
        pos.qty = new_qty  # частичное закрытие: цена входа остатка не меняется
    else:
        pos.qty, pos.entry = new_qty, price  # переворот: остаток открыт по цене сделки
    return realized


def unrealized(pos: Position, price: float | None) -> float | None:
    """Текущий (незафиксированный) результат позиции по цене price, ₽."""
    if not price or pos.entry is None or not pos.qty:
        return None
    return (price - pos.entry) * pos.qty


def unrealized_pct(pos: Position, price: float | None) -> float | None:
    """Результат в процентах от цены входа с учётом стороны: для шорта рост цены — минус."""
    if not price or not pos.entry or not pos.qty:
        return None
    sign = 1 if pos.qty > 0 else -1
    return (price - pos.entry) / pos.entry * 100 * sign


def record_trade(ticker: str, side: str, qty: float, price: float, realized: float,
                 when: datetime | None = None, path: Path | None = None) -> None:
    """Добавляет сделку в журнал data/trades.json."""
    path = path or config.TRADES_FILE
    trades = read_json(path, [])
    if not isinstance(trades, list):
        trades = []
    trades.append({
        "time": (when or datetime.now(config.MSK)).isoformat(timespec="seconds"),
        "ticker": ticker,
        "side": side,
        "qty": qty,
        "price": price,
        "realized": round(realized, 2),
    })
    write_json(path, trades)


def fmt_qty(qty: float) -> str:
    return f"{qty:g}"


def portfolio_lines(positions: dict[str, Position], prices: dict[str, float | None]) -> list[str]:
    """Таблица позиций с итогами. prices: тикер -> текущая цена."""
    if not positions:
        return ["Позиций нет. Добавьте их командами /buy, /sell или /pos (см. /help)."]
    lines = ["Тикер | Кол-во | Вход | Сейчас | Результат | Стоимость"]
    total_unrealized = 0.0
    total_realized = 0.0
    long_value = short_value = 0.0
    missing: list[str] = []
    for ticker, pos in sorted(positions.items()):
        total_realized += pos.realized
        if not pos.qty:
            lines.append(f"{ticker} | закрыта | | | зафиксировано {pos.realized:+.2f} ₽ |")
            continue
        price = prices.get(ticker)
        result = unrealized(pos, price)
        entry = f"{pos.entry:.2f}" if pos.entry is not None else "н/д"
        if price and result is not None:
            total_unrealized += result
            value = abs(pos.qty) * price
            if pos.qty > 0:
                long_value += value
            else:
                short_value += value
            pct = unrealized_pct(pos, price)
            lines.append(
                f"{ticker} | {fmt_qty(pos.qty)} ({pos.side}) | {entry} | {price:.2f} | "
                f"{result:+.2f} ₽ ({pct:+.1f}%) | {value:,.0f} ₽".replace(",", " ")
            )
        else:
            missing.append(ticker)
            lines.append(f"{ticker} | {fmt_qty(pos.qty)} ({pos.side}) | {entry} | нет цены | н/д |")
        if pos.realized:
            lines.append(f"    зафиксировано ранее по {ticker}: {pos.realized:+.2f} ₽")
    lines.append("")
    lines.append(f"Текущий результат открытых позиций: {total_unrealized:+.2f} ₽")
    if total_realized:
        lines.append(f"Зафиксировано по закрытым частям: {total_realized:+.2f} ₽")
        lines.append(f"Всего: {total_unrealized + total_realized:+.2f} ₽")
    lines.append(f"Стоимость лонгов: {long_value:,.0f} ₽".replace(",", " ")
                 + (f" | размер шортов: {short_value:,.0f} ₽".replace(",", " ") if short_value else ""))
    for ticker, pos in sorted(positions.items()):
        price = prices.get(ticker)
        if pos.qty < 0 and price:
            per_pct = abs(pos.qty) * price / 100
            lines.append(f"Риск шорта {ticker}: рост цены на 1% (≈{price / 100:.2f} ₽) = −{per_pct:.2f} ₽")
    if missing:
        lines.append("Нет цены для: " + ", ".join(missing) + " (добавьте их в список наблюдения)")
    lines.append("Комиссии и плата за перенос шорта не учтены.")
    return lines
