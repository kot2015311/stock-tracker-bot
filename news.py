"""Новости: общий формат NewsItem, RSS-ленты, новости Мосбиржи и отбор по вашим бумагам.

Функции parse_* и select_news ничего не скачивают, поэтому их проверяют тесты без интернета.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import config
from config import MSK
from storage import read_json

MOEX_NEWS_URL = "https://iss.moex.com/iss/sitenews.json"
MOEX_ARTICLE_URL = "https://www.moex.com/n{id}"
ATOM = "{http://www.w3.org/2005/Atom}"

# Основы слов для поиска. Основа «сбер» найдёт «Сбербанк», «Сбера», «Сбербанка».
# Строка с префиксом re: — это регулярное выражение (для сложных случаев).
DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "SBER": ["re:(?<!\\w)сбер(?!инвест)", "sber", "греф"],
    "GMKN": ["норникел", "норильск", "nornickel", "gmkn", "потанин"],
    "X5": ["x5", "х5", "икс 5", "пятерочк", "перекресток", "чижик"],  # латинская x и русская х
    "LKOH": ["лукойл", "lukoil", "lkoh"],
    "NVTK": ["новатэк", "novatek", "nvtk", "михельсон", "арктик спг", "ямал спг"],
    "OZON": ["re:(?<!\\w)озон(?!\\s*фарм)", "re:(?<!\\w)ozon(?!\\s*pharm)"],
    "OZPH": ["озон фарм", "ozon pharm", "ozph"],
    "TATN": ["татнефт", "tatn"],
    "GAZP": ["re:(?<!\\w)газпром(?!\\s*нефт)", "gazp"],
    "SPBE": ["спб бирж", "spbe"],
    "MOEX": ["мосбирж", "московская бирж", "moex"],
    "T": ["т-технолог", "т-банк", "тинькофф"],
    "YDEX": ["яндекс", "yandex", "ydex"],
    "ROSN": ["роснефт", "rosn", "сечин"],
    "PLZL": ["полюс", "plzl"],
    "CHMF": ["северстал", "chmf"],
    "MGNT": ["магнит", "mgnt"],
}

# Темы, которые двигают весь рынок. Новости с ними попадают в раздел «Макро».
DEFAULT_MACRO: list[str] = [
    "ключевую ставк", "ключевой ставк", "ключевая ставк", "ставку цб", "ставки цб", "ставка цб", "снижение ставки", "повышение ставки", "инфляц",
    "банк росси", "центробанк", "набиуллин", "минфин", "курс рубл", "курс доллар", "курс юан",
    "рубль укреп", "рубль ослаб", "укрепление рубл", "ослабление рубл", "нефт", "brent", "urals", "опек",
    "цены на газ", "газ в европ", "спг", "санкц", "пошлин", "мосбирж", "индекс мосбирж", "imoex", "офз",
    "фрс", "уиткофф", "переговоры по украин", "re:(?<!\\w)ввп(?!\\w)",
]

# Служебные сообщения биржи, которые почти никогда не нужны для решений.
MOEX_NOISE = [
    "облигац", "список ценных бумаг", "дестабилизац", "программ", "проспект", "значении индексов",
    "о начале торгов", "перечн", "репо", "ставки переноса", "ценового коридора", "риск-параметр",
]


@dataclass
class NewsItem:
    source: str
    published: datetime | None
    title: str
    link: str
    text: str = ""  # полный текст: посты Telegram, присланные вами материалы, описание из RSS
    kind: str = "feed"  # feed — лента новостей, analytics — обзоры брокеров, manual — прислали вы
    uid: str = ""
    added: datetime | None = None  # когда бот узнал о новости
    tags: list[str] = field(default_factory=list)

    @property
    def date_text(self) -> str:
        return self.published.strftime("%Y-%m-%d %H:%M") if self.published else "дата не указана"

    @property
    def moment(self) -> datetime | None:
        return self.published or self.added

    def body(self) -> str:
        return self.text or self.title

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "source": self.source, "kind": self.kind, "title": self.title,
            "link": self.link, "text": self.text, "tags": self.tags,
            "published": self.published.isoformat() if self.published else None,
            "added": self.added.isoformat() if self.added else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsItem":
        def dt(value):
            if not value:
                return None
            try:
                moment = datetime.fromisoformat(value)
            except ValueError:
                return None
            return moment if moment.tzinfo else moment.replace(tzinfo=MSK)

        return cls(
            source=data.get("source", ""), published=dt(data.get("published")), title=data.get("title", ""),
            link=data.get("link", ""), text=data.get("text", ""), kind=data.get("kind", "feed"),
            uid=data.get("uid", ""), added=dt(data.get("added")), tags=list(data.get("tags") or []),
        )


# ---------------------------------------------------------------- разбор данных
def _parse_rss_date(raw: str | None) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)  # формат RSS: Fri, 16 Oct 2026 12:46:00 +0300
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))  # формат Atom
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def strip_html(raw: str | None, limit: int = 600) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = " ".join(html.unescape(text).split())
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def parse_moex_news(data: dict) -> list[NewsItem]:
    """Ответ ISS /sitenews.json -> список новостей. Время у биржи московское."""
    block = data["sitenews"]
    columns = block["columns"]
    items: list[NewsItem] = []
    for row in block["data"]:
        rec = dict(zip(columns, row))
        published = None
        raw = rec.get("published_at")
        if raw:
            try:
                published = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
            except ValueError:
                published = None
        link = MOEX_ARTICLE_URL.format(id=rec["id"]) if rec.get("id") else ""
        items.append(NewsItem("Мосбиржа", published, html.unescape(rec.get("title") or "").strip(), link,
                              uid=f"moex:{rec.get('id')}"))
    return items


def parse_rss(body: bytes, source_name: str, limit: int = 80) -> list[NewsItem]:
    """RSS 2.0 (и простой Atom) -> список новостей. Читаем байты: кодировка берётся из самого XML."""
    root = ET.fromstring(body)
    items: list[NewsItem] = []
    for node in root.iter("item"):
        title = html.unescape((node.findtext("title") or "").strip())
        link = (node.findtext("link") or "").strip()
        items.append(NewsItem(
            source_name, _parse_rss_date(node.findtext("pubDate")), title, link,
            text=strip_html(node.findtext("description")), uid=f"rss:{link or title}",
        ))
        if len(items) >= limit:
            break
    if not items:  # запасной вариант: формат Atom
        for node in root.iter(ATOM + "entry"):
            link_node = node.find(ATOM + "link")
            link = link_node.get("href", "") if link_node is not None else ""
            title = html.unescape((node.findtext(ATOM + "title") or "").strip())
            items.append(NewsItem(
                source_name,
                _parse_rss_date(node.findtext(ATOM + "updated") or node.findtext(ATOM + "published")),
                title, link, text=strip_html(node.findtext(ATOM + "summary")), uid=f"rss:{link or title}",
            ))
            if len(items) >= limit:
                break
    for item in items:
        if item.text and _norm(item.text).startswith(_norm(item.title)) and len(item.text) <= len(item.title) + 3:
            item.text = ""  # описание повторяет заголовок
    return items


async def fetch_moex_news(http) -> list[NewsItem]:
    return parse_moex_news(await http.get_json(MOEX_NEWS_URL, {"iss.meta": "off"}))


async def fetch_rss(http, source: dict) -> list[NewsItem]:
    return parse_rss(await http.get_bytes(source["url"]), source.get("name") or source["url"])


# ---------------------------------------------------------------- ключевые слова
def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def compile_words(words: list[str]):
    """Список основ слов (или re:выражений) -> одно регулярное выражение."""
    parts = []
    for word in words:
        word = str(word).strip()
        if not word:
            continue
        if word.startswith("re:"):
            parts.append(f"(?:{word[3:]})")
        else:
            parts.append(r"(?<!\w)" + re.escape(_norm(word)))  # основа должна стоять в начале слова
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def name_stem(name: str) -> str:
    """'Татнефть ап' -> 'татнефт': грубая основа первого слова названия компании."""
    word = re.split(r"[^\w-]+", _norm(name).strip())[0] if name else ""
    return word[:-1] if len(word) > 6 else word


def load_keywords(watch: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Ключевые слова по бумагам из списка наблюдения и слова макро-тем.

    Свои слова задаются в config/keywords.json: {"tickers": {"SBER": [...]}, "macro": [...]}.
    """
    custom = read_json(config.KEYWORDS_FILE, {})
    if not isinstance(custom, dict):
        custom = {}
    custom_tickers = custom.get("tickers") if isinstance(custom.get("tickers"), dict) else {
        k: v for k, v in custom.items() if isinstance(v, list) and k.upper() == k and k != "MACRO"
    }
    keywords: dict[str, list[str]] = {}
    for ticker, item in watch.items():
        words = custom_tickers.get(ticker) or DEFAULT_KEYWORDS.get(ticker)
        if not words:  # неизвестная бумага: ищем по тикеру и основе названия
            words = [ticker.lower()] + ([name_stem(item.get("name", ""))] if item.get("name") else [])
        keywords[ticker] = [str(w) for w in words if str(w).strip()]
    macro = custom.get("macro") if isinstance(custom.get("macro"), list) else DEFAULT_MACRO
    return keywords, [str(w) for w in macro]


_MOEX_NOISE_RE = None


def is_moex_noise(item: NewsItem) -> bool:
    global _MOEX_NOISE_RE
    if item.source != "Мосбиржа":
        return False
    if _MOEX_NOISE_RE is None:
        _MOEX_NOISE_RE = compile_words(MOEX_NOISE)
    return bool(_MOEX_NOISE_RE.search(_norm(item.title)))


def is_tag_line(text: str) -> bool:
    """Строка только из хэштегов, флагов и эмодзи, например «🇷🇺#SBER #дивиденды»."""
    return not re.sub(r"#\w+|[^\w]|_", "", text)


def excerpt(item: NewsItem, pattern, limit: int = 380) -> str:
    """Абзац, в котором встретилось ключевое слово, одной строкой. Если слово только в заголовке — начало текста."""
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n+", item.body()) if p.strip()]
    chosen = next((p for p in paragraphs if pattern.search(_norm(p))), None)
    if chosen is not None and is_tag_line(chosen):
        # Слово нашлось только в хэштегах (#SBER): показываем первый содержательный абзац
        chosen = next((p for p in paragraphs if not is_tag_line(p)), chosen)
    if chosen is None:
        chosen = " ".join(paragraphs) or item.title
    return chosen if len(chosen) <= limit else chosen[:limit].rstrip() + "…"


# ---------------------------------------------------------------- отбор для отчёта
@dataclass
class Mention:
    item: NewsItem
    excerpt: str
    is_new: bool = False


@dataclass
class NewsSelection:
    by_ticker: dict[str, list[Mention]]
    analytics: list[NewsItem]  # обзоры брокеров с прошлого отчёта, полный текст
    macro: list[Mention]  # макро-темы из новостных лент
    manual_new: list[NewsItem]  # присланные вами материалы с прошлого отчёта
    manual_old: list[NewsItem]  # присланные раньше (только заголовки)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", _norm(text)) if len(w) > 3}


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Одна новость из нескольких лент нужна один раз: похожие заголовки (70% общих слов) считаются дублями."""
    kept: list[tuple[set[str], NewsItem]] = []
    for item in items:
        words = _words(item.title)
        duplicate = False
        for other_words, other in kept:
            if item.kind == "manual" or other.kind == "manual":
                continue  # ваши материалы не схлопываются
            if min(len(words), len(other_words)) < 3:
                same = " ".join(_norm(item.title).split()) == " ".join(_norm(other.title).split())
                if same:
                    duplicate = True
                    break
                continue
            overlap = len(words & other_words) / min(len(words), len(other_words))
            if overlap >= 0.7:
                    duplicate = True
                    break
        if not duplicate:
            kept.append((words, item))
    return [item for _, item in kept]


SERVICE_POST = re.compile(r"pinned a|закрепил|^\s*(?:опрос|голосуем)", re.IGNORECASE)


# Слова «про рынок». Длинный пост без ваших бумаг и макро-тем попадает в обзоры, только если в нём
# не меньше двух разных таких слов: так отсеиваются развлекательные посты брокеров («заработал на покемонах»).
MARKET_WORDS = re.compile(
    r"акци[ийяюе]|бумаг|рынк|рынок|индекс|облигац|дивиденд|купон|отчетн|прибыл|выручк|ebitda|мсфо|рсбу"
    r"|капитализац|котировк|биржев|ход торгов|сектор|эмитент|(?<!\w)ipo(?!\w)|фьючерс|доходност"
    r"|металлург|нефтегаз|девелопер|ритейл|целев\w* цен|аналитик|\d\s?%"
)
MIN_MARKET_WORDS = 2


def is_useful_review(item: NewsItem, patterns: list) -> bool:
    """Обзор брокера попадает в отчёт, если он про ваши бумаги, макро или рынок, а не опрос, мем или служебный пост."""
    body = item.body()
    if SERVICE_POST.search(body):
        return False
    norm = _norm(body)
    if any(p and p.search(norm) for p in patterns):
        return True
    found = {m.group(0) for m in MARKET_WORDS.finditer(norm)}
    return len(found) >= MIN_MARKET_WORDS


def select_news(
    items: list[NewsItem],
    keywords: dict[str, list[str]],
    macro_words: list[str],
    now: datetime,
    since: datetime | None = None,
    *,
    ticker_days: int = 3,
    per_ticker: int = 5,
    analytics_limit: int = 20,
    macro_limit: int = 15,
    manual_days: int = 3,
) -> NewsSelection:
    since = since or now - timedelta(hours=24)
    oldest = datetime.min.replace(tzinfo=MSK)
    ordered = dedupe(sorted(items, key=lambda n: n.moment or oldest, reverse=True))

    def is_after(item: NewsItem, border: datetime) -> bool:
        return (item.moment or oldest) > border

    patterns = {ticker: compile_words(words) for ticker, words in keywords.items()}
    by_ticker: dict[str, list[Mention]] = {ticker: [] for ticker in keywords}
    used: set[str] = set()
    ticker_border = now - timedelta(days=ticker_days)
    for item in ordered:
        if not is_after(item, ticker_border) or is_moex_noise(item):
            continue
        haystack = _norm(item.title + "\n" + item.text)
        for ticker, pattern in patterns.items():
            if pattern and len(by_ticker[ticker]) < per_ticker and pattern.search(haystack):
                by_ticker[ticker].append(Mention(item, excerpt(item, pattern), is_after(item, since)))
                used.add(item.uid or item.title)
    by_ticker = {t: lst for t, lst in by_ticker.items() if lst}

    macro_pattern = compile_words(macro_words)
    review_patterns = list(patterns.values()) + [macro_pattern]
    analytics = [i for i in ordered if i.kind == "analytics" and is_after(i, since)
                 and is_useful_review(i, review_patterns)]
    analytics = sorted(analytics[:analytics_limit], key=lambda n: n.moment or oldest)

    macro: list[Mention] = []
    if macro_pattern:
        for item in ordered:
            if len(macro) >= macro_limit:
                break
            if item.kind != "feed" or not is_after(item, since) or (item.uid or item.title) in used:
                continue
            if is_moex_noise(item):
                continue
            if macro_pattern.search(_norm(item.title + "\n" + item.text[:400])):
                macro.append(Mention(item, excerpt(item, macro_pattern, 300), True))

    manual = [i for i in ordered if i.kind == "manual"]
    manual_new = sorted([i for i in manual if (i.added or i.moment or oldest) > since], key=lambda n: n.added or oldest)
    manual_border = now - timedelta(days=manual_days)
    manual_old = [i for i in manual if since >= (i.added or i.moment or oldest) > manual_border]
    return NewsSelection(by_ticker, analytics, macro, manual_new, manual_old)
