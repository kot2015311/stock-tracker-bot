"""Чтение публичных Telegram-каналов без входа в аккаунт через веб-просмотр t.me/s/<канал>.

Страница отдаёт около 20 последних постов. Чтобы ничего не пропустить, бот запоминает
номер последнего прочитанного поста и при необходимости листает назад (?before=<номер>).
Работает только для открытых каналов, у которых включён веб-просмотр.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from config import MSK
from news import NewsItem

CHANNEL_URL = "https://t.me/s/{channel}"
POST_LINK_RE = re.compile(r"^https?://(?:www\.)?t(?:elegram)?\.me/(?:s/)?([A-Za-z0-9_]{4,})/(\d+)")


@dataclass
class ChannelPost:
    channel: str
    post_id: int
    published: datetime | None
    text: str

    @property
    def link(self) -> str:
        return f"https://t.me/{self.channel}/{self.post_id}"


def _node_text(node) -> str:
    """Текст поста с переносами строк. Эмодзи и ссылки сохраняются как текст."""
    for br in node.find_all("br"):
        br.replace_with("\n")
    text = node.get_text()
    lines = [line.strip() for line in text.replace("\xa0", " ").split("\n")]
    cleaned: list[str] = []
    for line in lines:
        if not line and (not cleaned or not cleaned[-1]):
            continue  # не больше одной пустой строки подряд
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def parse_channel_page(html_text: str, channel: str) -> list[ChannelPost]:
    """HTML страницы t.me/s/<канал> -> посты от старых к новым. Посты без текста пропускаются."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    messages = soup.select("div.tgme_widget_message[data-post]")
    if not messages and not soup.select_one(".tgme_channel_info, .tgme_widget_message_wrap"):
        raise ValueError("это не страница канала: канал не найден или закрыт для веб-просмотра")
    posts: list[ChannelPost] = []
    for msg in messages:
        data_post = msg.get("data-post", "")
        name, _, number = data_post.rpartition("/")
        if not number.isdigit():
            continue
        text_node = msg.select_one("div.tgme_widget_message_text.js-message_text")
        if text_node is None:
            nodes = [n for n in msg.select("div.tgme_widget_message_text")
                     if not n.find_parent(class_="tgme_widget_message_reply")]
            text_node = nodes[0] if nodes else None
        text = _node_text(text_node) if text_node is not None else ""
        if not text:
            continue  # только картинка или видео без подписи
        published = None
        time_node = msg.select_one("a.tgme_widget_message_date time[datetime]") or msg.select_one("time[datetime]")
        if time_node is not None:
            try:
                published = datetime.fromisoformat(time_node["datetime"].replace("Z", "+00:00")).astimezone(MSK)
            except ValueError:
                published = None
        posts.append(ChannelPost(name or channel, int(number), published, text))
    posts.sort(key=lambda p: p.post_id)
    return posts


def first_line(text: str, limit: int = 160) -> str:
    """Заголовок поста: первая содержательная строка."""
    from news import is_tag_line

    for line in text.split("\n"):
        line = line.strip(" •-—*")
        if len(line) >= 3 and not is_tag_line(line):
            return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return text[:limit]


def post_to_news(post: ChannelPost, source_name: str, kind: str, added: datetime | None = None,
                 max_text: int = 4000) -> NewsItem:
    text = post.text if len(post.text) <= max_text else post.text[:max_text].rstrip() + "…"
    return NewsItem(
        source=source_name,
        published=post.published,
        title=first_line(post.text),
        link=post.link,
        text=text,
        kind=kind,
        uid=f"tg:{post.channel.lower()}/{post.post_id}",
        added=added,
    )


async def fetch_channel_posts(
    http,
    channel: str,
    after_id: int | None = None,
    *,
    max_pages: int = 4,
    max_age: timedelta = timedelta(days=3),
    now: datetime | None = None,
) -> list[ChannelPost]:
    """Новые посты канала после after_id (или за последние дни при первом запуске)."""
    now = now or datetime.now(MSK)
    border = now - max_age
    url = CHANNEL_URL.format(channel=channel)
    collected: dict[int, ChannelPost] = {}
    params: dict | None = None
    for _page in range(max_pages):
        posts = parse_channel_page(await http.get_text(url, params), channel)
        if not posts:
            break
        for post in posts:
            collected[post.post_id] = post
        oldest = posts[0]
        reached_known = after_id is not None and oldest.post_id <= after_id + 1
        too_old = oldest.published is not None and oldest.published < border
        if after_id is None or reached_known or too_old:
            break  # при первом запуске хватает одной страницы
        params = {"before": oldest.post_id}
    result = [p for p in collected.values()
              if (after_id is None or p.post_id > after_id) and (p.published is None or p.published >= border)]
    return sorted(result, key=lambda p: p.post_id)


async def fetch_single_post(http, url: str) -> ChannelPost | None:
    """Один пост по ссылке вида https://t.me/<канал>/<номер>."""
    match = POST_LINK_RE.match(url.strip())
    if not match:
        return None
    channel, number = match.group(1), int(match.group(2))
    posts = parse_channel_page(await http.get_text(CHANNEL_URL.format(channel=channel), {"before": number + 1}), channel)
    for post in posts:
        if post.post_id == number:
            return post
    return None
