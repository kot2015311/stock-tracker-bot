"""Технические показатели по дневным свечам: изменения, диапазоны, средние, RSI, ATR, объём.

Это описание того, что уже произошло с ценой. Показатели не предсказывают будущее,
но помогают быстро увидеть, где цена находится относительно своей истории.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from market_data import Candle, pct_change


@dataclass
class TechStats:
    last_day: date
    last_close: float
    day_pct: float | None  # к предыдущей торговой сессии
    week_pct: float | None  # к закрытию 5 торговых дней назад
    month_pct: float | None  # к закрытию 20 торговых дней назад
    low_20: float  # минимум за последние 20 торговых дней
    high_20: float  # максимум за последние 20 торговых дней
    volume_ratio: float | None  # объём последней сессии к среднему за 20 предыдущих
    prev_volume_ratio: float | None  # то же для предыдущей сессии (она уже точно полная)
    sma20: float | None
    sma50: float | None
    sma200: float | None
    rsi14: float | None
    atr14: float | None
    low_52w: float | None
    high_52w: float | None
    candles: int


def sma(values: list[float], period: int) -> float | None:
    """Простая скользящая средняя последних period значений."""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Индекс относительной силы по Уайлдеру: выше 70 — перекупленность, ниже 30 — перепроданность."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes, closes[1:]):
        diff = cur - prev
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr(candles: list[Candle], period: int = 14) -> float | None:
    """Средний истинный диапазон по Уайлдеру: обычный дневной размах цены в рублях."""
    if len(candles) < period + 1:
        return None
    ranges = []
    for prev, cur in zip(candles, candles[1:]):
        ranges.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    value = sum(ranges[:period]) / period
    for tr in ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def _volume_ratio(candles: list[Candle], idx: int) -> float | None:
    """Объём свечи idx к среднему объёму 20 свечей перед ней."""
    if idx < 1 or idx >= len(candles) or not candles[idx].volume:
        return None
    earlier = [c.volume for c in candles[max(0, idx - 20):idx] if c.volume]
    return candles[idx].volume / (sum(earlier) / len(earlier)) if earlier else None


def compute_stats(candles: list[Candle]) -> TechStats | None:
    if len(candles) < 2:
        return None
    closes = [c.close for c in candles]
    last = candles[-1]
    window = candles[-20:]
    year = candles[-250:]
    has_year = len(candles) >= 120  # годовой диапазон показываем, только если истории хватает
    return TechStats(
        last_day=last.day,
        last_close=last.close,
        day_pct=pct_change(last.close, candles[-2].close),
        week_pct=pct_change(last.close, candles[-6].close) if len(candles) >= 6 else None,
        month_pct=pct_change(last.close, candles[-21].close) if len(candles) >= 21 else None,
        low_20=min(c.low for c in window),
        high_20=max(c.high for c in window),
        volume_ratio=_volume_ratio(candles, len(candles) - 1),
        prev_volume_ratio=_volume_ratio(candles, len(candles) - 2),
        sma20=sma(closes, 20),
        sma50=sma(closes, 50),
        sma200=sma(closes, 200),
        rsi14=rsi(closes, 14),
        atr14=atr(candles, 14),
        low_52w=min(c.low for c in year) if has_year else None,
        high_52w=max(c.high for c in year) if has_year else None,
        candles=len(candles),
    )


def _rel(price: float, level: float | None) -> float | None:
    return pct_change(price, level) if level else None


def describe(stats: TechStats, price: float | None = None) -> list[str]:
    """Короткие факты о положении цены. Только описание, без советов."""
    p = price or stats.last_close
    facts: list[str] = []

    for label, level in (("200-дневной", stats.sma200), ("50-дневной", stats.sma50)):
        diff = _rel(p, level)
        if diff is not None:
            side = "выше" if diff >= 0 else "ниже"
            facts.append(f"цена {side} {label} средней ({level:.2f}) на {abs(diff):.1f}%")

    if stats.rsi14 is not None:
        if stats.rsi14 >= 70:
            zone = "зона перекупленности (выше 70)"
        elif stats.rsi14 <= 30:
            zone = "зона перепроданности (ниже 30)"
        else:
            zone = "нейтрально (30–70)"
        facts.append(f"RSI(14) {stats.rsi14:.0f}: {zone}")

    if stats.high_20 > stats.low_20:
        position = (p - stats.low_20) / (stats.high_20 - stats.low_20) * 100
        if p < stats.low_20:
            facts.append(f"цена ниже 20-дневного минимума {stats.low_20:.2f}")
        elif p > stats.high_20:
            facts.append(f"цена выше 20-дневного максимума {stats.high_20:.2f}")
        elif position <= 10:
            facts.append(f"у нижней границы 20-дневного диапазона ({stats.low_20:.2f}–{stats.high_20:.2f})")
        elif position >= 90:
            facts.append(f"у верхней границы 20-дневного диапазона ({stats.low_20:.2f}–{stats.high_20:.2f})")

    if stats.high_52w and p >= stats.high_52w * 0.98:
        facts.append(f"около годового максимума {stats.high_52w:.2f}")
    elif stats.low_52w and p <= stats.low_52w * 1.02:
        facts.append(f"около годового минимума {stats.low_52w:.2f}")

    if stats.atr14:
        share = stats.atr14 / p * 100
        facts.append(f"обычный дневной размах (ATR14) ≈ {stats.atr14:.2f} ₽ ({share:.{1 if share >= 1 else 2}f}%)")

    if stats.prev_volume_ratio and stats.prev_volume_ratio >= 1.5:
        facts.append(f"в прошлую сессию объём был в {stats.prev_volume_ratio:.1f} раза выше среднего")
    return facts
