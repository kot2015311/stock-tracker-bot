"""Проверка всех источников данных из вашей сети. Запуск из папки проекта:

    python check_sources.py            проверить и показать результат
    python check_sources.py --apply    ещё и включить рабочие источники, а неработающие выключить

То же самое делает команда /check в боте. Токены берутся из .env и нигде не показываются.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse

import config
from config import MSK
from http_client import describe_error, open_http
from indicators import compute_stats
from market_data import fetch_brent, fetch_candles, fetch_cbr_rates, fetch_key_rate
from news import fetch_moex_news, fetch_rss
from news_store import load_sources
from storage import read_json, write_json
from telegram_channels import fetch_channel_posts

SBER_FIGI = "BBG004730N88"
ARTICLE_TEST_URL = "https://alfabank.ru/alfa-investor/t/5-aktsiy-kotorye-seychas-horosho-pokupayut-210926/"


@dataclass
class CheckResult:
    group: str
    name: str
    ok: bool
    detail: str
    host: str = ""
    key: str = ""  # для --apply: "tg:<канал>" или "rss:<адрес>"


async def _run(group: str, name: str, coro, host: str = "", key: str = "") -> CheckResult:
    try:
        return CheckResult(group, name, True, await coro, host, key)
    except Exception as exc:  # noqa: BLE001 - нам нужен текст любой ошибки
        return CheckResult(group, name, False, describe_error(exc), host, key)


async def _telegram_api(http, token: str) -> str:
    data = await http.get_json(f"https://api.telegram.org/bot{token}/getMe")
    if not data.get("ok"):
        raise ValueError("Telegram ответил ошибкой: проверьте BOT_TOKEN")
    return f"бот @{data['result'].get('username')}"


async def _tinkoff(market) -> str:
    prices = await market.prices([SBER_FIGI])
    price = prices.get(SBER_FIGI)
    if not price:
        raise ValueError("API ответил, но цены нет")
    return f"цена SBER {price:.2f} ₽"


async def _candles(http, secid: str, index: bool = False) -> str:
    candles = await fetch_candles(http, secid, index=index, today=date.today())
    stats = compute_stats(candles)
    if not stats:
        raise ValueError("слишком мало свечей")
    sma = f", SMA200 {stats.sma200:.2f}" if stats.sma200 else ", для SMA200 мало истории"
    return f"{len(candles)} свечей, последняя {stats.last_day}: {stats.last_close:.2f}{sma}"


async def _dividends(market) -> str:
    items = await market.dividends(SBER_FIGI)
    return f"{len(items)} записей, последняя {items[0].record_date}: {items[0].value:g}" if items else "0 записей"


async def _brent(http) -> str:
    quote = await fetch_brent(http)
    return f"{quote.secid}: ${quote.price:.2f} (до {quote.expiry})"


async def _rates(http) -> str:
    rates = await fetch_cbr_rates(http)
    usd = rates.rates.get("USD")
    return f"курсы на {rates.date}, USD {usd[0]:.2f}" if usd else f"курсы на {rates.date}"


async def _key_rate(http) -> str:
    rate = await fetch_key_rate(http)
    return f"ключевая ставка {rate.value:.2f}% (с {rate.since})"


async def _channel(http, channel: str) -> str:
    posts = await fetch_channel_posts(http, channel, None, now=datetime.now(MSK))
    if not posts:
        return "канал открыт, но за 3 дня нет текстовых постов"
    last = posts[-1]
    when = last.published.strftime("%Y-%m-%d %H:%M") if last.published else "без даты"
    return f"{len(posts)} постов, последний {when}: {last.text.splitlines()[0][:60]}"


async def _rss(http, feed: dict) -> str:
    items = await fetch_rss(http, feed)
    if not items:
        raise ValueError("лента пустая или это не RSS")
    return f"{len(items)} новостей, свежая {items[0].date_text}: {items[0].title[:60]}"


async def _moex_news(http) -> str:
    items = await fetch_moex_news(http)
    return f"{len(items)} новостей, свежая {items[0].date_text}" if items else "0 новостей"


async def _article(http) -> str:
    from articles import fetch_article

    article = await fetch_article(http, ARTICLE_TEST_URL)
    return f"достал {len(article.text)} символов: «{article.title[:50]}»"


async def run_checks(http, *, market=None, bot_token: str = "", sources: dict | None = None) -> list[CheckResult]:
    sources = sources if sources is not None else load_sources()
    jobs = []
    if bot_token:
        jobs.append(_run("Основное", "Telegram Bot API", _telegram_api(http, bot_token), "api.telegram.org"))
    if market is not None:
        jobs.append(_run("Основное", "Т-Инвестиции API (цены)", _tinkoff(market), "invest-public-api.tbank.ru"))
        jobs.append(_run("Основное", "Т-Инвестиции API (дивиденды SBER)", _dividends(market), "invest-public-api.tbank.ru"))
    jobs += [
        _run("Основное", "ISS: свечи SBER", _candles(http, "SBER"), "iss.moex.com"),
        _run("Основное", "ISS: индекс IMOEX", _candles(http, "IMOEX", index=True), "iss.moex.com"),
        _run("Основное", "ISS: фьючерс Brent", _brent(http), "iss.moex.com"),
        _run("Основное", "ЦБ: курсы валют", _rates(http), "www.cbr-xml-daily.ru"),
        _run("Основное", "ЦБ: ключевая ставка", _key_rate(http), "www.cbr.ru"),
        _run("Основное", "Текст статьи по ссылке (Альфа)", _article(http), "alfabank.ru"),
    ]
    for ch in sources["telegram"]:
        state = "" if ch.get("enabled", True) else " [выключен]"
        name = f"{ch.get('name') or ch['channel']} (@{ch['channel']}){state}"
        jobs.append(_run("Telegram-каналы", name, _channel(http, ch["channel"]), "t.me", "tg:" + ch["channel"]))
    for feed in sources["rss"]:
        state = "" if feed.get("enabled", True) else " [выключена]"
        jobs.append(_run("RSS-ленты", f"{feed.get('name') or feed['url']}{state}", _rss(http, feed),
                         urlparse(feed["url"]).hostname or "", "rss:" + feed["url"]))
    if sources.get("moex_sitenews"):
        jobs.append(_run("RSS-ленты", "Мосбиржа: новости сайта", _moex_news(http), "iss.moex.com"))
    return list(await asyncio.gather(*jobs))


def format_results(results: list[CheckResult], bundle: str | None = None) -> str:
    lines = [f"ПРОВЕРКА ИСТОЧНИКОВ {datetime.now(MSK):%Y-%m-%d %H:%M} (МСК)"]
    lines.append(f"Сертификаты: {bundle or 'системные'}")
    group = None
    for r in results:
        if r.group != group:
            group = r.group
            lines += ["", group]
        lines.append(f"{'✅' if r.ok else '❌'} {r.name}: {r.detail}")
    ok = sum(r.ok for r in results)
    lines += ["", f"Работает {ok} из {len(results)}."]
    network_words = ("нет соединения", "таймаут", "HTTP 403", "ошибка сертификата")
    failed_hosts = sorted({r.host for r in results if not r.ok and r.host and r.detail.startswith(network_words)})
    if failed_hosts:
        ru = [h for h in failed_hosts if h.endswith(".ru")]
        other = [h for h in failed_hosts if not h.endswith(".ru")]
        if ru:
            lines.append("Российские сайты часто не пускают зарубежные IP. Если включён VPN, добавьте для них "
                         "правило «Прямое» (без VPN): " + ", ".join(ru))
        if other:
            lines.append("Эти адреса, наоборот, могут требовать VPN: " + ", ".join(other))
    return "\n".join(lines)


def apply_results(results: list[CheckResult]) -> list[str]:
    """Выключает неработающие источники и включает обратно те, что выключались из-за сбоя. Возвращает список изменений."""
    data = read_json(config.SOURCES_FILE, {})
    if not isinstance(data, dict):
        return ["config/sources.json не прочитан, ничего не меняю"]
    by_key = {r.key: r for r in results if r.key}
    changes = []
    for section, prefix, field in (("telegram", "tg:", "channel"), ("rss", "rss:", "url")):
        for src in data.get(section, []):
            result = by_key.get(prefix + str(src.get(field)))
            if result is None:
                continue
            was = src.get("enabled", True)
            if result.ok and not was and "note" not in src:
                continue  # выключен вами или по умолчанию — не включаем сами
            src["enabled"] = result.ok
            if result.ok:
                src.pop("note", None)
            else:
                src["note"] = f"выключен {date.today()}: {result.detail}"
            if was != result.ok:
                changes.append(f"{'включён' if result.ok else 'выключен'}: {src.get('name') or src.get(field)}")
    write_json(config.SOURCES_FILE, data)
    return changes


async def main() -> None:
    config.load_env()
    bundle = config.setup_ca_bundle()
    import os

    market = None
    token = os.getenv("TINKOFF_TOKEN", "").strip()
    if token:
        try:
            from tinkoff_client import TinkoffMarket

            market = TinkoffMarket(token)
        except ImportError:
            print("SDK Т-Инвестиций не установлен: проверка цен пропущена (см. README)")
    async with open_http() as http:
        results = await run_checks(http, market=market, bot_token=os.getenv("BOT_TOKEN", "").strip())
    print(format_results(results, bundle))
    if "--apply" in sys.argv:
        changes = apply_results(results)
        print()
        print("Изменения в config/sources.json:" if changes else "config/sources.json: менять нечего.")
        for change in changes:
            print("  " + change)
    else:
        print("\nЧтобы включить рабочие и выключить неработающие источники: python check_sources.py --apply")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # эмодзи и русский текст в консоли Windows
        except (AttributeError, ValueError):
            pass
    asyncio.run(main())
