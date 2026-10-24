"""Рыночные данные: свечи, дивиденды и фьючерс Brent (ISS Мосбиржи), курсы и ключевая ставка (ЦБ РФ).

Функции parse_* ничего не скачивают, только разбирают ответ. Поэтому их проверяют тесты без интернета.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

ISS = "https://iss.moex.com/iss"
SHARE_CANDLES_URL = ISS + "/engines/stock/markets/shares/boards/TQBR/securities/{secid}/candles.json"
INDEX_CANDLES_URL = ISS + "/engines/stock/markets/index/boards/SNDX/securities/{secid}/candles.json"
DIVIDENDS_URL = ISS + "/securities/{secid}/dividends.json"
FUTURES_URL = ISS + "/engines/futures/markets/forts/boards/RFUD/securities.json"
CBR_DAILY_URL = "https://www.cbr-xml-daily.ru/daily_json.js"  # зеркало официальных курсов ЦБ в JSON
KEY_RATE_URL = "https://www.cbr.ru/hd_base/KeyRate/"
HISTORY_DAYS = 420  # ~290 торговых дней: хватает на 200-дневную среднюю и годовой диапазон


@dataclass
class Candle:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Dividend:
    record_date: str
    value: float
    currency: str
    last_buy_date: str = ""  # последний день покупки под дивиденд (даёт Т-Инвестиции)
    payment_date: str = ""


@dataclass
class CbrRates:
    date: str
    rates: dict[str, tuple[float, float]]  # код -> (курс сейчас, курс на предыдущую дату)


@dataclass
class FuturesQuote:
    secid: str
    name: str
    price: float
    prev_settle: float | None
    expiry: str

    @property
    def change_pct(self) -> float | None:
        return pct_change(self.price, self.prev_settle)


@dataclass
class KeyRate:
    value: float  # действующая ставка, % годовых
    since: str  # с какой даты действует (ДД.ММ.ГГГГ)
    previous: float | None  # прошлое значение


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return (new - old) / old * 100


def _float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN -> None


# ---------------------------------------------------------------- разбор данных
def parse_candles(data: dict) -> list[Candle]:
    """Ответ ISS /candles.json -> свечи от старых к новым."""
    block = data["candles"]
    columns = block["columns"]
    candles: list[Candle] = []
    for row in block["data"]:
        rec = dict(zip(columns, row))
        close = rec.get("close")
        if close is None:
            continue
        day = datetime.strptime(str(rec["begin"])[:10], "%Y-%m-%d").date()
        candles.append(Candle(
            day=day,
            open=float(rec.get("open") or close),
            high=float(rec.get("high") or close),
            low=float(rec.get("low") or close),
            close=float(close),
            volume=float(rec.get("volume") or 0),
        ))
    candles.sort(key=lambda c: c.day)
    return candles


def parse_dividends(data: dict) -> list[Dividend]:
    """Ответ ISS /securities/<тикер>/dividends.json -> дивиденды от новых к старым."""
    if not isinstance(data, dict) or "dividends" not in data:
        have = ", ".join(data) if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"в ответе ISS нет блока dividends, есть: {have or 'пусто'}")
    block = data["dividends"]
    columns = block["columns"]
    result: list[Dividend] = []
    for row in block["data"]:
        rec = dict(zip(columns, row))
        if rec.get("value") is None:
            continue
        currency = str(rec.get("currencyid") or "RUB")
        result.append(Dividend(
            record_date=str(rec.get("registryclosedate") or "дата не указана")[:10],
            value=float(rec["value"]),
            currency="RUB" if currency == "SUR" else currency,
        ))
    result.sort(key=lambda d: d.record_date, reverse=True)
    return result


def _sdk_date(value) -> str:
    """Дата из SDK -> 'ГГГГ-ММ-ДД'. Пустые даты SDK приходят как 1970 год, их считаем отсутствующими."""
    if value is None or getattr(value, "year", 0) < 2000:
        return ""
    return value.strftime("%Y-%m-%d")


def parse_tinkoff_dividends(items) -> list[Dividend]:
    """Ответ GetDividends (Т-Инвестиции) -> дивиденды на 1 акцию, от новых к старым. Включает объявленные будущие."""
    result: list[Dividend] = []
    for item in items or []:
        money = getattr(item, "dividend_net", None)
        if money is None:
            continue
        value = money.units + money.nano / 1e9
        if value <= 0:
            continue
        record = _sdk_date(getattr(item, "record_date", None))
        last_buy = _sdk_date(getattr(item, "last_buy_date", None))
        result.append(Dividend(
            record_date=record or last_buy or "дата не указана",
            value=value,
            currency=(money.currency or "rub").upper(),
            last_buy_date=last_buy,
            payment_date=_sdk_date(getattr(item, "payment_date", None)),
        ))
    result.sort(key=lambda d: d.record_date, reverse=True)
    return result


def parse_cbr_rates(data: dict, codes: tuple[str, ...] = ("USD", "EUR", "CNY")) -> CbrRates:
    """JSON cbr-xml-daily.ru -> курсы за 1 единицу валюты (у некоторых валют номинал 10 или 100)."""
    rates: dict[str, tuple[float, float]] = {}
    for code in codes:
        item = data["Valute"].get(code)
        if item:
            nominal = item.get("Nominal") or 1
            rates[code] = (item["Value"] / nominal, item["Previous"] / nominal)
    return CbrRates(date=str(data.get("Date", ""))[:10], rates=rates)


def _block_rows(data: dict, name: str) -> list[dict]:
    block = data.get(name) or {}
    columns = block.get("columns") or []
    return [dict(zip(columns, row)) for row in block.get("data") or []]


def parse_futures(data: dict, asset: str = "BR", today: date | None = None) -> FuturesQuote | None:
    """Ответ ISS по фьючерсам -> основной (самый торгуемый) контракт на актив, например BR (нефть Brent)."""
    today = today or date.today()
    market = {row.get("SECID"): row for row in _block_rows(data, "marketdata")}
    candidates = []
    for sec in _block_rows(data, "securities"):
        if str(sec.get("ASSETCODE") or "").upper() != asset:
            continue
        expiry = str(sec.get("LASTTRADEDATE") or "")[:10]
        if expiry and expiry < today.isoformat():
            continue
        md = market.get(sec.get("SECID"), {})
        price = (_float(md.get("LAST")) or _float(md.get("SETTLEPRICE")) or _float(md.get("LASTSETTLEPRICE"))
                 or _float(sec.get("PREVSETTLEPRICE")) or _float(sec.get("PREVPRICE")))
        if not price:
            continue
        open_interest = _float(md.get("OPENPOSITION")) or _float(sec.get("PREVOPENPOSITION")) or 0.0
        candidates.append((open_interest, expiry, sec, price))
    if not candidates:
        return None
    if any(c[0] for c in candidates):
        best = max(candidates, key=lambda c: c[0])  # основной контракт: больше всего открытых позиций
    else:
        best = min(candidates, key=lambda c: c[1] or "9999")  # иначе ближайший по сроку
    _, expiry, sec, price = best
    return FuturesQuote(
        secid=str(sec.get("SECID")),
        name=str(sec.get("SHORTNAME") or sec.get("SECID")),
        price=price,
        prev_settle=_float(sec.get("PREVSETTLEPRICE")),
        expiry=expiry,
    )


_KEY_RATE_ROW = re.compile(r"<td[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td[^>]*>\s*([\d\s]+[,.]\d+)\s*</td>")


def parse_key_rate(html_text: str) -> KeyRate:
    """Страница ЦБ «Ключевая ставка» -> действующая ставка, дата начала и прошлое значение."""
    rows = []
    for day_text, value_text in _KEY_RATE_ROW.findall(html_text):
        day = datetime.strptime(day_text, "%d.%m.%Y").date()
        rows.append((day, float(value_text.replace(" ", "").replace(",", "."))))
    if not rows:
        raise ValueError("на странице ЦБ не нашлась таблица ключевой ставки")
    rows.sort()  # от старых к новым
    current = rows[-1][1]
    since, previous = rows[0][0], None
    for day, value in reversed(rows):
        if value != current:
            previous = value
            break
        since = day
    return KeyRate(value=current, since=since.strftime("%d.%m.%Y"), previous=previous)


# ---------------------------------------------------------------- загрузка
async def fetch_candles(http, secid: str, *, index: bool = False, today: date | None = None,
                        days: int = HISTORY_DAYS) -> list[Candle]:
    url = (INDEX_CANDLES_URL if index else SHARE_CANDLES_URL).format(secid=secid)
    start = (today or date.today()) - timedelta(days=days)
    data = await http.get_json(url, {"from": start.isoformat(), "interval": 24, "iss.meta": "off"})
    return parse_candles(data)


async def fetch_dividends(http, secid: str) -> list[Dividend]:
    data = await http.get_json(DIVIDENDS_URL.format(secid=secid), {"iss.meta": "off"})
    return parse_dividends(data)


async def fetch_cbr_rates(http) -> CbrRates:
    return parse_cbr_rates(await http.get_json(CBR_DAILY_URL))


async def fetch_brent(http, today: date | None = None) -> FuturesQuote:
    data = await http.get_json(FUTURES_URL, {"iss.meta": "off", "iss.only": "securities,marketdata"})
    quote = parse_futures(data, "BR", today)
    if quote is None:
        raise ValueError("в ответе ISS нет действующих фьючерсов BR")
    return quote


async def fetch_key_rate(http, today: date | None = None) -> KeyRate:
    today = today or date.today()
    params = {
        "UniDbQuery.Posted": "True",
        "UniDbQuery.From": (today - timedelta(days=200)).strftime("%d.%m.%Y"),
        "UniDbQuery.To": today.strftime("%d.%m.%Y"),
    }
    return parse_key_rate(await http.get_text(KEY_RATE_URL, params))
