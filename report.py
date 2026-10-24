"""Сборка отчёта для анализа: портфель, рынок, цены и индикаторы, дивиденды, новости, ваши материалы.

Каждый источник независим. Если один не ответил, отчёт всё равно соберётся,
а причина сбоя попадёт в раздел «ПРОБЛЕМЫ ИСТОЧНИКОВ» в конце.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config
from alerts import load_alerts
from config import MSK
from http_client import describe_error, open_http
from indicators import TechStats, compute_stats, describe
from market_data import (
    fetch_brent,
    fetch_candles,
    fetch_cbr_rates,
    fetch_dividends,
    fetch_key_rate,
    pct_change,
)
from news import NewsItem, load_keywords, select_news
from plan import load_plan, plan_lines
from portfolio import load_portfolio, portfolio_lines
from storage import read_json, write_json

DISCLAIMER = "Источники: Т-Инвестиции API, ISS Мосбиржи, ЦБ РФ, Telegram-каналы и RSS-ленты. Это данные, а не инвестиционная рекомендация."
MAX_POST_CHARS = 2500  # длинные обзоры обрезаются, ссылка на полный текст остаётся


@dataclass
class Report:
    text: str
    created: datetime
    data: dict = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return f"report_{self.created:%Y%m%d_%H%M}.txt"


# ---------------------------------------------------------------- небольшие помощники
def _pct(value: float | None) -> str:
    return "н/д" if value is None else f"{value:+.2f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "н/д" if value is None else f"{value:.{digits}f}"


def _short(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def load_state() -> dict:
    state = read_json(config.STATE_FILE, {})
    return state if isinstance(state, dict) else {}


def last_report_time() -> datetime | None:
    raw = load_state().get("last_report_at")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def mark_report_sent(report: Report) -> None:
    state = load_state()
    state["last_report_at"] = report.created.isoformat(timespec="seconds")
    write_json(config.STATE_FILE, state)


def save_report_files(report: Report, keep_days: int = 60) -> None:
    """Копия отчёта (txt) и цифр (json) в data/reports. Старше keep_days удаляются."""
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = config.REPORTS_DIR / report.filename
    base.write_text(report.text, encoding="utf-8")
    write_json(base.with_suffix(".json"), report.data)
    border = datetime.now().timestamp() - keep_days * 86400
    for old in config.REPORTS_DIR.glob("report_*"):
        try:
            if old.stat().st_mtime < border:
                old.unlink()
        except OSError:
            pass


async def _get_dividends(market, http, ticker: str, figi: str, now: datetime) -> list:
    """Сначала Т-Инвестиции (там есть будущие выплаты и дата «купить до»), при сбое ISS Мосбиржи."""
    problems: list[str] = []
    if figi and hasattr(market, "dividends"):
        try:
            found = await market.dividends(figi, now)
            if found:
                return found
            problems.append("Т-Инвестиции: пусто")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Т-Инвестиции: {describe_error(exc)}")
    try:
        return await fetch_dividends(http, ticker)
    except Exception as exc:  # noqa: BLE001
        if not problems:
            raise
        problems.append(f"ISS: {describe_error(exc)}")
        raise RuntimeError("; ".join(problems)) from exc


def _session_in_progress(now: datetime, last_session: date | None) -> bool:
    """Торги на Мосбирже идут по будням примерно с 6:50 до 23:50, объём за день до конца неполный."""
    return bool(last_session) and last_session == now.date() and now.weekday() < 5 and (now.hour, now.minute) < (23, 50)


# ---------------------------------------------------------------- основная функция
async def build_report(market, watch: dict, *, store=None, http=None, now: datetime | None = None,
                       since: datetime | None = None, collect: bool = True) -> Report:
    """Собирает отчёт. store — хранилище новостей (NewsStore); since — время прошлого отчёта."""
    now = now or datetime.now(MSK)
    if http is None:
        async with open_http() as client:
            return await _build(market, watch, client, store, now, since, collect)
    return await _build(market, watch, http, store, now, since, collect)


async def _build(market, watch: dict, http, store, now: datetime, since: datetime | None, collect: bool) -> Report:
    errors: list[str] = []
    today = now.date()
    tickers = list(watch)
    sem = asyncio.Semaphore(5)  # не заваливаем биржу запросами

    async def guarded(label: str, coro):
        try:
            async with sem:
                return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - отчёт должен собраться при любом сбое источника
            errors.append(f"{label}: {describe_error(exc)}")
            return None

    async def nothing():
        return None

    figis = [item["figi"] for item in watch.values() if item.get("figi")]
    named = {
        "prices": guarded("Т-Инвестиции (цены)", market.prices(figis)) if figis else nothing(),
        "rates": guarded("ЦБ РФ (курсы валют)", fetch_cbr_rates(http)),
        "key_rate": guarded("ЦБ РФ (ключевая ставка)", fetch_key_rate(http, today)),
        "brent": guarded("Мосбиржа (фьючерс Brent)", fetch_brent(http, today)),
        "imoex": guarded("ISS индекс IMOEX", fetch_candles(http, "IMOEX", index=True, today=today)),
        "rgbi": guarded("ISS индекс RGBI", fetch_candles(http, "RGBI", index=True, today=today)),
        "news": guarded("Сбор новостей", store.collect(http, now=now)) if (store is not None and collect) else nothing(),
    }
    candle_jobs = [guarded(f"ISS свечи {t}", fetch_candles(http, t, today=today)) for t in tickers]
    dividend_jobs = [guarded(f"Дивиденды {t}", _get_dividends(market, http, t, watch[t].get("figi", ""), now))
                     for t in tickers]
    results = await asyncio.gather(*named.values(), *candle_jobs, *dividend_jobs)
    got = dict(zip(named, results[:len(named)]))
    candles_by = dict(zip(tickers, results[len(named):len(named) + len(tickers)]))
    dividends_by = dict(zip(tickers, results[len(named) + len(tickers):]))

    prices: dict = got["prices"] or {}
    stats: dict[str, TechStats | None] = {t: compute_stats(c) if c else None for t, c in candles_by.items()}
    if got["news"] is not None:
        errors.extend(f"Новости — {e}" for e in got["news"].errors)

    # Итоговая цена по каждой бумаге: Т-Инвестиции, при сбое — закрытие из ISS
    final_price: dict[str, float | None] = {}
    fallback_used = False
    for ticker, item in watch.items():
        price = prices.get(item.get("figi")) or None
        if not price and stats.get(ticker):
            price, fallback_used = stats[ticker].last_close, True
        final_price[ticker] = price

    positions = load_portfolio()
    lines: list[str] = [f"ОТЧЁТ НА {now:%Y-%m-%d %H:%M} (МСК)"]
    if since:
        lines.append(f"Прошлый отчёт: {since:%Y-%m-%d %H:%M}. Разделы «обзоры», «макро» и «ваши материалы» — с этого времени.")
    else:
        lines.append("Прошлого отчёта нет: разделы «обзоры», «макро» и «ваши материалы» — за последние 24 часа.")
    lines.append("")

    # --- портфель
    lines.append("ПОРТФЕЛЬ (data/portfolio.json; меняется командами /buy, /sell, /pos)")
    lines += portfolio_lines(positions, final_price)
    alerts = load_alerts()
    if alerts:
        parts = []
        for alert in alerts:
            price = final_price.get(alert.ticker)
            distance = f", до уровня {pct_change(alert.level, price):+.1f}%" if price else ""
            parts.append(f"{alert.describe()} (сейчас {_num(price)}{distance})")
        lines.append("Уровни для уведомлений: " + "; ".join(parts))
    lines.append("")
    plan = load_plan()
    if plan:
        lines.append("ПЛАН ПО ПОЗИЦИЯМ (data/plan.json; для лонга выход — ниже уровня, для шорта — выше)")
        lines += plan_lines(plan, positions, final_price)
        lines.append("")

    # --- рынок
    lines.append("РЫНОК")
    imoex = compute_stats(got["imoex"]) if got["imoex"] else None
    if imoex:
        extra = ""
        if imoex.sma200:
            extra = f" | к 200-дневной средней ({imoex.sma200:.0f}) {_pct(pct_change(imoex.last_close, imoex.sma200))}"
        lines.append(
            f"Индекс МосБиржи: {imoex.last_close:.2f} | день {_pct(imoex.day_pct)} | 5д {_pct(imoex.week_pct)} | "
            f"20д {_pct(imoex.month_pct)}{extra} | RSI {_num(imoex.rsi14, 0)} (сессия {imoex.last_day})"
        )
    else:
        lines.append("Индекс МосБиржи: нет данных")
    rgbi = compute_stats(got["rgbi"]) if got["rgbi"] else None
    if rgbi:
        lines.append(f"RGBI (гособлигации; растёт, когда рынок ждёт снижения ставки): {rgbi.last_close:.2f} | "
                     f"день {_pct(rgbi.day_pct)} | 20д {_pct(rgbi.month_pct)}")
    brent = got["brent"]
    if brent:
        lines.append(f"Нефть Brent (фьючерс {brent.secid} на Мосбирже, до {brent.expiry}): ${brent.price:.2f} "
                     f"({_pct(brent.change_pct)} к прошлой расчётной цене)")
    rates = got["rates"]
    if rates and rates.rates:
        parts = [f"{code} {value:.2f} ({_pct(pct_change(value, previous))})" for code, (value, previous) in rates.rates.items()]
        lines.append(f"Курсы ЦБ РФ на {rates.date}: " + " | ".join(parts))
    key_rate = got["key_rate"]
    if key_rate:
        if key_rate.previous is not None:
            lines.append(f"Ключевая ставка ЦБ: {key_rate.value:.2f}% с {key_rate.since} (до этого {key_rate.previous:.2f}%)")
        else:
            lines.append(f"Ключевая ставка ЦБ: {key_rate.value:.2f}% (без изменений как минимум с {key_rate.since})")
    lines.append("")

    # --- цены и динамика
    last_session = max((s.last_day for s in stats.values() if s), default=None)
    lines.append("ЦЕНЫ И ДИНАМИКА")
    if last_session:
        lines.append(f"Последняя торговая сессия по данным ISS: {last_session}")
        if (today - last_session).days > 5:
            lines.append("ВНИМАНИЕ: свечи ISS старше 5 дней, динамика может быть неточной")
    lines.append("Тикер | Цена | К прошл. отчёту | День | 5 дн | 20 дн | Диапазон 20 дн | RSI14 | К SMA50 | К SMA200 | Объём посл./пред.")
    previous_prices = read_json(config.LAST_PRICES_FILE, {})
    if not isinstance(previous_prices, dict):
        previous_prices = {}
    fresh_prices: dict[str, float] = {}
    for ticker, item in watch.items():
        price = final_price.get(ticker)
        if not price:
            lines.append(f"{ticker} | цена не получена")
            continue
        from_tinkoff = bool(prices.get(item.get("figi")))
        mark = "" if from_tinkoff else "*"
        if from_tinkoff:
            fresh_prices[ticker] = price
        prev = previous_prices.get(ticker)
        since_report = _pct(pct_change(price, prev)) if prev else "первый отчёт"
        st = stats.get(ticker)
        if st:
            vol = f"x{_num(st.volume_ratio, 1)} / x{_num(st.prev_volume_ratio, 1)}"
            lines.append(
                f"{ticker} | {price:.2f} ₽{mark} | {since_report} | {_pct(st.day_pct)} | {_pct(st.week_pct)} | "
                f"{_pct(st.month_pct)} | {st.low_20:.2f}-{st.high_20:.2f} | {_num(st.rsi14, 0)} | "
                f"{_pct(pct_change(price, st.sma50))} | {_pct(pct_change(price, st.sma200))} | {vol}"
            )
        else:
            lines.append(f"{ticker} | {price:.2f} ₽{mark} | {since_report} | нет данных ISS о динамике")
    if _session_in_progress(now, last_session):
        lines.append("Торги сегодня ещё идут: объём последней сессии неполный, смотрите на «пред.».")
    if fallback_used:
        lines.append("* цена закрытия из ISS: Т-Инвестиции не вернули цену по этой бумаге")
    write_json(config.LAST_PRICES_FILE, {**previous_prices, **fresh_prices})
    lines.append("")

    # --- техническая картина
    lines.append("ТЕХНИЧЕСКАЯ КАРТИНА (описание истории цены, не прогноз)")
    for ticker in tickers:
        st = stats.get(ticker)
        if st:
            lines.append(f"{ticker}: " + "; ".join(describe(st, final_price.get(ticker))))
    lines.append("")

    # --- дивиденды
    lines.append("ДИВИДЕНДЫ (на 1 акцию; решения проверяйте на e-disclosure.ru)")
    upcoming: list[str] = []
    for ticker in tickers:
        for d in dividends_by.get(ticker) or []:
            key_day = d.last_buy_date or d.record_date
            if key_day[:4].isdigit() and key_day >= today.isoformat():
                text = f"{ticker}: {d.value:g} {d.currency}, отсечка {d.record_date}"
                if d.last_buy_date:
                    days_left = (date.fromisoformat(d.last_buy_date) - today).days
                    text += f", купить до {d.last_buy_date} (через {days_left} дн.)"
                pos = positions.get(ticker)
                if pos and pos.qty > 0:
                    text += f"; на ваши {pos.qty:g} шт ≈ {d.value * pos.qty:,.0f} ₽ до налога".replace(",", " ")
                elif pos and pos.qty < 0:
                    text += f"; у вас шорт {abs(pos.qty):g} шт: если держать его через отсечку, дивиденд ≈ " \
                            f"{d.value * abs(pos.qty):,.0f} ₽ спишут с вас".replace(",", " ")
                upcoming.append(text)
    if upcoming:
        lines.append("Объявленные будущие выплаты:")
        lines += [f"- {u}" for u in upcoming]
    else:
        lines.append("Объявленных будущих выплат по вашим бумагам не найдено.")
    lines.append("История (последние 3 записи):")
    for ticker in tickers:
        dividends = dividends_by.get(ticker)
        if dividends:
            parts = [f"{d.record_date}: {d.value:g} {d.currency}" for d in dividends[:3]]
            lines.append(f"{ticker}: " + "; ".join(parts))
        else:
            lines.append(f"{ticker}: нет данных")
    lines.append("")

    # --- новости
    selection = None
    if store is not None:
        keywords, macro_words = load_keywords(watch)
        selection = select_news(store.visible(), keywords, macro_words, now, since)
        lines += _news_sections(selection)
        counts: dict[str, int] = {}
        for item in store.visible():
            counts[item.kind] = counts.get(item.kind, 0) + 1
        lines.append(
            f"В базе новостей: каналы и ленты — {counts.get('feed', 0)}, обзоры — {counts.get('analytics', 0)}, "
            f"ваши материалы — {counts.get('manual', 0)}."
        )
        lines.append("")

    # --- проблемы
    if errors:
        lines.append("ПРОБЛЕМЫ ИСТОЧНИКОВ (отчёт собран без этих данных; проверка: /check или python check_sources.py)")
        lines += [f"- {e}" for e in errors]
        lines.append("")
    lines.append(DISCLAIMER)

    data = {
        "created": now.isoformat(timespec="seconds"),
        "since": since.isoformat(timespec="seconds") if since else None,
        "prices": final_price,
        "positions": {t: {"qty": p.qty, "entry": p.entry, "realized": p.realized} for t, p in positions.items()},
        "stats": {
            t: {k: (v.isoformat() if isinstance(v, date) else v) for k, v in vars(s).items()}
            for t, s in stats.items() if s
        },
        "imoex": imoex.last_close if imoex else None,
        "brent": brent.price if brent else None,
        "key_rate": key_rate.value if key_rate else None,
        "usd": rates.rates.get("USD", (None,))[0] if rates and rates.rates else None,
        "errors": errors,
    }
    return Report(text="\n".join(lines), created=now, data=data)


def _item_header(item: NewsItem) -> str:
    link = f" | {item.link}" if item.link else ""
    return f"=== {item.date_text} | {item.source}{link}"


def _news_sections(sel) -> list[str]:
    lines: list[str] = []
    lines.append("НОВОСТИ ПО ВАШИМ БУМАГАМ (за 3 дня; [новое] — вышло после прошлого отчёта)")
    if sel.by_ticker:
        for ticker, mentions in sel.by_ticker.items():
            lines.append(ticker)
            for m in mentions:
                flag = "[новое] " if m.is_new else ""
                text = m.excerpt
                if m.item.kind != "feed" and not text.startswith(m.item.title[:40]):
                    text = f"«{_short(m.item.title, 80)}»: {text}"  # из какого обзора эта строка
                lines.append(f"- {flag}{m.item.date_text} [{m.item.source}] {_short(text, 450)}")
                if m.item.link:
                    lines.append(f"  {m.item.link}")
    else:
        lines.append("Упоминаний ваших бумаг в подключённых каналах и лентах не найдено.")
    lines.append("")

    lines.append("ОБЗОРЫ И КОММЕНТАРИИ БРОКЕРОВ (Telegram-каналы, полный текст)")
    if sel.analytics:
        for item in sel.analytics:
            lines.append(_item_header(item))
            lines.append(_short(item.body(), MAX_POST_CHARS))
            lines.append("")
    else:
        lines.append("Новых обзоров нет.")
        lines.append("")

    lines.append("МАКРО И РЫНОК (новостные ленты)")
    if sel.macro:
        for m in sel.macro:
            lines.append(f"- {m.item.date_text} [{m.item.source}] {_short(m.excerpt, 300)}")
            if m.item.link:
                lines.append(f"  {m.item.link}")
    else:
        lines.append("Новых новостей по макро-темам нет.")
    lines.append("")

    lines.append("ВАШИ МАТЕРИАЛЫ (то, что вы прислали боту)")
    if sel.manual_new:
        for item in sel.manual_new:
            lines.append(_item_header(item))
            lines.append(_short(item.body(), 12000))
            lines.append("")
    else:
        lines.append("Новых материалов нет. Перешлите боту пост из канала, ссылку или текст — он попадёт сюда.")
        lines.append("")
    if sel.manual_old:
        lines.append("Присланные ранее (полный текст был в прошлых отчётах):")
        for item in sel.manual_old:
            added = item.added.strftime("%Y-%m-%d %H:%M") if item.added else item.date_text
            lines.append(f"- {added} {item.title}" + (f" | {item.link}" if item.link else ""))
        lines.append("")
    return lines
