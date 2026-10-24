import asyncio
import unittest
from datetime import datetime, timedelta

from helpers import FakeHttp, TempDataCase, channel_html

from config import MSK
from news import NewsItem
from news_store import NewsStore, load_sources

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=MSK)
RSS = ('<?xml version="1.0" encoding="utf-8"?><rss><channel>'
       "<item><title>Новатэк запустил линию СПГ</title><link>https://x/1</link>"
       "<pubDate>Fri, 23 Oct 2026 11:00:00 +0300</pubDate></item></channel></rss>")
SOURCES = {
    "telegram": [{"channel": "alfa", "name": "Альфа", "kind": "analytics"},
                 {"channel": "off", "name": "Выключен", "enabled": False}],
    "rss": [{"name": "Лента", "url": "https://feed/rss"}, {"name": "Сломана", "url": "https://broken/rss"}],
    "moex_sitenews": False,
}


def page(numbers):
    return channel_html("alfa", [(n, "2026-10-23T08:00:00+00:00", f"Обзор {n}") for n in numbers])


class StoreTest(TempDataCase):
    def test_collect_save_and_cursor(self):
        http = FakeHttp({"t.me/s/alfa": page([10, 11]), "feed/rss": RSS, "broken/rss": ConnectionError("нет сети")})
        store = NewsStore()
        result = asyncio.run(store.collect(http, SOURCES, now=NOW))
        self.assertEqual(result.new, 3)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Сломана", result.errors[0])
        self.assertEqual(store.cursors["tg:alfa"], 11)
        self.assertNotIn(("https://t.me/s/off", None), http.calls)  # выключенный канал не трогаем

        again = NewsStore()  # читается из файла
        self.assertEqual(len(again.all()), 3)
        kinds = {i.uid: i.kind for i in again.all()}
        self.assertEqual(kinds["tg:alfa/11"], "analytics")
        self.assertEqual(kinds["rss:https://x/1"], "feed")

        http2 = FakeHttp({"t.me/s/alfa": page([10, 11, 12]), "feed/rss": RSS, "broken/rss": RSS})
        result2 = asyncio.run(again.collect(http2, SOURCES, now=NOW))
        self.assertEqual(result2.new, 1)  # только пост 12; RSS-новость уже была

    def test_prune_by_kind(self):
        store = NewsStore()
        old = NOW - timedelta(days=5)
        store.add([
            NewsItem("Лента", old, "Старая новость", "", uid="a"),
            NewsItem("Альфа", old, "Обзор недели", "", kind="analytics", uid="b"),
        ], now=NOW)
        store.add_manual("Моя заметка", "Текст", now=old)
        store.prune(NOW)
        self.assertEqual(sorted(i.title for i in store.all()), ["Моя заметка", "Обзор недели"])

    def test_manual_items(self):
        store = NewsStore()
        store.add_manual("Статья", "Текст статьи", link="https://a/1", now=NOW)
        store.add_manual("Статья", "Текст статьи v2", link="https://a/1", now=NOW)  # та же ссылка — обновление
        self.assertEqual(len(store.manual()), 1)
        self.assertEqual(store.manual()[0].text, "Текст статьи v2")
        self.assertEqual(store.clear_manual(), 1)
        self.assertEqual(NewsStore().manual(), [])


class SourcesTest(TempDataCase):
    def test_real_config_is_valid(self):
        sources = load_sources()
        self.assertGreaterEqual(len(sources["telegram"]), 5)
        self.assertTrue(any(c["channel"] == "alfa_investments" for c in sources["telegram"]))
        self.assertTrue(all(s["url"].startswith("https://") for s in sources["rss"]))


class ApplyTest(TempDataCase):
    def test_apply_keeps_default_disabled_sources_off(self):
        import json

        import config
        from check_sources import CheckResult, apply_results

        path = self.data_dir / "sources.json"
        path.write_text(json.dumps({"telegram": [], "rss": [
            {"name": "ТАСС", "url": "https://tass/rss", "enabled": False},
            {"name": "Сбой", "url": "https://was-broken/rss", "enabled": False, "note": "выключен: таймаут"},
            {"name": "Ломается", "url": "https://breaks/rss", "enabled": True},
        ]}), encoding="utf-8")
        saved, config.SOURCES_FILE = config.SOURCES_FILE, path
        try:
            apply_results([CheckResult("RSS", "ТАСС", True, "ok", key="rss:https://tass/rss"),
                           CheckResult("RSS", "Сбой", True, "ok", key="rss:https://was-broken/rss"),
                           CheckResult("RSS", "Ломается", False, "таймаут", key="rss:https://breaks/rss")])
        finally:
            config.SOURCES_FILE = saved
        state = {s["name"]: s["enabled"] for s in json.loads(path.read_text(encoding="utf-8"))["rss"]}
        self.assertEqual(state, {"ТАСС": False, "Сбой": True, "Ломается": False})


if __name__ == "__main__":
    unittest.main()
