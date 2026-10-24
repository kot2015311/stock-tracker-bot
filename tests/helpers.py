"""Общие подделки для тестов: сеть, биржа, временная папка data.

Тесты работают без интернета, без токенов и без aiogram/aiohttp/SDK Т-Инвестиций.
Реальную связь с источниками проверяет check_sources.py (или /check в боте).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeHttp:
    """Ответ выбирается по куску адреса (и параметру before для страниц Telegram).

    Значение-исключение будет выброшено, как будто сайт не ответил.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    @staticmethod
    def _value(value):
        if isinstance(value, Exception):
            raise value
        return value

    def _find(self, url: str, params: dict | None):
        self.calls.append((url, params))
        if params and "before" in params:
            target = f"{url}?before={params['before']}"
            for key, value in self.routes.items():
                if "?before=" in key and target.endswith(key):
                    return self._value(value)
        for key, value in self.routes.items():
            if "?before=" not in key and key in url:
                return self._value(value)
        raise RuntimeError(f"в тесте нет ответа для {url} {params or ''}")

    async def get_json(self, url, params=None):
        return self._find(url, params)

    async def get_bytes(self, url, params=None):
        value = self._find(url, params)
        return value.encode("utf-8") if isinstance(value, str) else value

    async def get_text(self, url, params=None):
        value = self._find(url, params)
        return value.decode("utf-8") if isinstance(value, bytes) else value


class FakeMarket:
    def __init__(self, prices: dict | None = None, dividends: dict | None = None, fail: Exception | None = None):
        self._prices = prices or {}
        self._dividends = dividends or {}
        self._fail = fail

    async def prices(self, figis):
        if self._fail:
            raise self._fail
        return {f: self._prices[f] for f in figis if f in self._prices}

    async def dividends(self, figi, now=None):
        value = self._dividends.get(figi, [])
        if isinstance(value, Exception):
            raise value
        return value

    async def find_share(self, ticker):
        return None


class TempDataCase(unittest.TestCase):
    """Каждый тест получает свою пустую папку data, чтобы не трогать настоящие файлы."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._saved = {"DATA_DIR": config.DATA_DIR, "NEWS_FILE": config.NEWS_FILE, "USERS_FILE": config.USERS_FILE}
        config.DATA_DIR = self.data_dir  # личные файлы считаются от DATA_DIR сами
        config.NEWS_FILE = self.data_dir / "news_store.json"
        config.USERS_FILE = self.data_dir / "users.json"

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        self._tmp.cleanup()


def make_candles_json(count: int = 30, start: date = date(2026, 9, 1), base: float = 100.0,
                      step: float = 1.0, last_volume: float = 2000.0) -> dict:
    """Синтетические свечи: закрытие base, base+step, ... и объём 1000 (у последней свечи свой)."""
    rows = []
    for i in range(count):
        close = base + i * step
        volume = last_volume if i == count - 1 else 1000.0
        day = (start + timedelta(days=i)).isoformat()
        rows.append([close - 0.5, close, close + 1, close - 1, close * volume, volume, f"{day} 00:00:00", f"{day} 23:59:59"])
    return {"candles": {"columns": ["open", "close", "high", "low", "value", "volume", "begin", "end"], "data": rows}}


def channel_html(channel: str, posts: list[tuple[int, str, str]], title: str = "Канал") -> str:
    """Страница t.me/s/<канал>: posts = [(номер, время ISO в UTC, html текста)]."""
    blocks = []
    for number, when, body in posts:
        text = f'<div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div>' if body else ""
        blocks.append(
            f'<div class="tgme_widget_message_wrap js-widget_message_wrap">'
            f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="{channel}/{number}">'
            f'<div class="tgme_widget_message_bubble">{text}'
            f'<div class="tgme_widget_message_footer"><div class="tgme_widget_message_info">'
            f'<span class="tgme_widget_message_views">1.2K</span>'
            f'<a class="tgme_widget_message_date" href="https://t.me/{channel}/{number}">'
            f'<time datetime="{when}" class="time">10:00</time></a></div></div></div></div></div>'
        )
    return (
        "<html><body><div class=\"tgme_channel_info\"><div class=\"tgme_channel_info_header_title\">"
        f"<span>{title}</span></div></div><section class=\"tgme_channel_history js-message_history\">"
        + "".join(blocks) + "</section></body></html>"
    )
