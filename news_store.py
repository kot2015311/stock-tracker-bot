"""Хранилище новостей (data/news_store.json) и сборщик из Telegram-каналов и RSS-лент.

Бот собирает новости по расписанию (по умолчанию раз в 10 минут), поэтому в отчёт попадает
всё, что вышло между отчётами, а не только последние 20 постов на странице канала.
Сюда же складываются материалы, которые вы присылаете боту.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import config
from config import MSK
from http_client import describe_error
from news import NewsItem, fetch_moex_news, fetch_rss
from storage import read_json, write_json
from telegram_channels import fetch_channel_posts, post_to_news
from users import is_own_manual, manual_tag

KEEP_DAYS = {"feed": 3, "analytics": 7, "manual": 14}  # сколько дней хранить новости каждого вида
MAX_ITEMS = 5000


def load_sources(path: Path | None = None) -> dict:
    """config/sources.json -> {"telegram": [...], "rss": [...], "moex_sitenews": bool}."""
    data = read_json(path or config.SOURCES_FILE, {})
    if isinstance(data, list):  # старый формат: просто список RSS-лент
        data = {"rss": data}
    if not isinstance(data, dict):
        data = {}
    return {
        "telegram": [c for c in data.get("telegram", []) if isinstance(c, dict) and c.get("channel")],
        "rss": [s for s in data.get("rss", []) if isinstance(s, dict) and s.get("url")],
        "moex_sitenews": bool(data.get("moex_sitenews", False)),
    }


def enabled(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("enabled", True)]


@dataclass
class CollectResult:
    new: int = 0
    errors: list[str] = field(default_factory=list)
    per_source: dict[str, int] = field(default_factory=dict)


class NewsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.NEWS_FILE
        self.items: dict[str, NewsItem] = {}
        self.cursors: dict[str, int] = {}  # номер последнего прочитанного поста по каждому каналу
        self.last_errors: list[str] = []
        self.last_collect: datetime | None = None
        self._lock = asyncio.Lock()
        self.load()

    # ---------------------------------------------------------------- файл
    def load(self) -> None:
        data = read_json(self.path, {})
        if not isinstance(data, dict):
            data = {}
        self.items = {}
        for raw in data.get("items", []):
            if isinstance(raw, dict) and raw.get("uid"):
                item = NewsItem.from_dict(raw)
                self.items[item.uid] = item
        self.cursors = {k: int(v) for k, v in (data.get("cursors") or {}).items() if str(v).isdigit()}

    def save(self) -> None:
        oldest = datetime.min.replace(tzinfo=MSK)
        items = sorted(self.items.values(), key=lambda n: n.moment or oldest)
        write_json(self.path, {"cursors": self.cursors, "items": [i.to_dict() for i in items]})

    # ---------------------------------------------------------------- данные
    def add(self, items: list[NewsItem], now: datetime | None = None) -> int:
        now = now or datetime.now(MSK)
        new = 0
        for item in items:
            if not item.uid:
                item.uid = "h:" + hashlib.sha1((item.source + item.title + item.link).encode("utf-8")).hexdigest()[:16]
            if item.uid in self.items:
                continue
            item.added = item.added or now
            self.items[item.uid] = item
            new += 1
        return new

    def prune(self, now: datetime | None = None) -> None:
        now = now or datetime.now(MSK)
        kept = {}
        for uid, item in self.items.items():
            moment = item.added if item.kind == "manual" else (item.moment or item.added)
            days = KEEP_DAYS.get(item.kind, 3)
            if moment is None or moment >= now - timedelta(days=days):
                kept[uid] = item
        if len(kept) > MAX_ITEMS:
            oldest = datetime.min.replace(tzinfo=MSK)
            newest = sorted(kept.values(), key=lambda n: n.moment or oldest, reverse=True)[:MAX_ITEMS]
            kept = {i.uid: i for i in newest}
        self.items = kept

    def all(self) -> list[NewsItem]:
        return list(self.items.values())

    def visible(self) -> list[NewsItem]:
        """Все новости плюс материалы только текущего пользователя."""
        return [i for i in self.items.values() if i.kind != "manual" or is_own_manual(i.tags)]

    def manual(self) -> list[NewsItem]:
        oldest = datetime.min.replace(tzinfo=MSK)
        return sorted([i for i in self.items.values() if i.kind == "manual" and is_own_manual(i.tags)],
                      key=lambda n: n.added or oldest, reverse=True)

    def clear_manual(self) -> int:
        manual = [uid for uid, item in self.items.items() if item.kind == "manual" and is_own_manual(item.tags)]
        for uid in manual:
            del self.items[uid]
        self.save()
        return len(manual)

    def add_manual(self, title: str, text: str, link: str = "", source: str = "Вы прислали",
                   published: datetime | None = None, now: datetime | None = None) -> NewsItem:
        now = now or datetime.now(MSK)
        tag = manual_tag()
        digest = hashlib.sha1((tag + (link or text)).encode("utf-8")).hexdigest()[:16]
        item = NewsItem(source=source, published=published or now, title=title, link=link, text=text,
                        kind="manual", uid=f"manual:{digest}", added=now, tags=[tag])
        self.items.pop(item.uid, None)  # повторная отправка того же материала обновляет его
        self.items[item.uid] = item
        self.save()
        return item

    # ---------------------------------------------------------------- сбор
    async def collect(self, http, sources: dict | None = None, now: datetime | None = None) -> CollectResult:
        """Скачивает все включённые источники параллельно и сохраняет новое. Ошибки не прерывают сбор."""
        sources = sources if sources is not None else load_sources()
        now = now or datetime.now(MSK)
        result = CollectResult()
        async with self._lock:  # два сбора одновременно (отчёт и фоновый) не нужны
            jobs: list[tuple[str, str, dict, asyncio.Future]] = []
            for channel in enabled(sources["telegram"]):
                key = "tg:" + channel["channel"].lower()
                coro = fetch_channel_posts(http, channel["channel"], self.cursors.get(key), now=now)
                jobs.append(("telegram", channel.get("name") or channel["channel"], channel, coro))
            for feed in enabled(sources["rss"]):
                jobs.append(("rss", feed.get("name") or feed["url"], feed, fetch_rss(http, feed)))
            if sources.get("moex_sitenews"):
                jobs.append(("moex", "Мосбиржа (новости)", {}, fetch_moex_news(http)))

            outcomes = await asyncio.gather(*(job[3] for job in jobs), return_exceptions=True)
            for (kind, name, src, _), outcome in zip(jobs, outcomes):
                if isinstance(outcome, BaseException):
                    if isinstance(outcome, asyncio.CancelledError):
                        raise outcome
                    result.errors.append(f"{name}: {describe_error(outcome)}")
                    continue
                if kind == "telegram":
                    key = "tg:" + src["channel"].lower()
                    items = [post_to_news(p, name, src.get("kind", "feed"), now) for p in outcome]
                    if outcome:
                        self.cursors[key] = max(self.cursors.get(key, 0), max(p.post_id for p in outcome))
                else:
                    items = outcome
                    for item in items:
                        item.kind = src.get("kind", "feed")
                added = self.add(items, now)
                result.new += added
                result.per_source[name] = added
            self.prune(now)
            self.save()
            self.last_errors = result.errors
            self.last_collect = now
        return result
