"""Материалы, которые вы присылаете боту: ссылки на статьи и скопированный текст страниц.

По ссылке бот скачивает страницу и достаёт из неё только текст статьи. Из скопированного
текста убирается мусор: меню сайта, комментарии, форма открытия счёта, подвал, повторы.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

URL_RE = re.compile(r"https?://[^\s<>\"'«»]+")

# Строки, которые целиком являются мусором (сравнение без регистра, после обрезки пробелов)
JUNK_EXACT = {
    "альфа‑рейтинг", "альфа-рейтинг", "календарь дивидендов", "обучение", "сообщество", "alfa-investments",
    "альфа‑инвестиции", "альфа-инвестиции", "главное сейчас", "войдите в профиль", "напишите комментарий",
    "правила модерации", "отредактировано", "дисклеймер", "новости", "альфа‑банк", "альфа-банк",
    "откройте брокерский счёт", "давайте познакомимся", "через госуслуги быстрее", "мобильный телефон",
    "дата рождения", "фамилия имя отчество", "укажите точно как в паспорте", "+7 (", "получите документы",
    "установите приложение", "заполните заявку онлайн", "как начать инвестировать", "читать далее",
    "поделиться", "подписаться", "реклама", "обсуждение ещё не началось", "напишите комментарий первым",
    "инвестиционный аналитик", "старший инвестиционный аналитик", "обозреватель", "автор статьи",
}
# Строки, содержащие эти фразы, тоже мусор
JUNK_CONTAINS = [
    "откройте бесплатный счёт", "откройте бесплатный счет", "открыть счёт возможно", "мы гарантируем безопасность",
    "и сохранность ваших данных", "нажимая «продолжить»", "согласие на обработку", "персональных данных",
    "файлы «cookie»", "файлы cookie", "генеральная лицензия банка россии", "© 20", "войдите и подпишите",
    "get-card-step", "шаг 1 из 3", "понадобится всего несколько минут", "счёт откроется с базовым",
    "или пользуйтесь альфа", "а вместе с ними можем привезти", "в мобильном приложении картинку можно увеличить",
    "применяются рекомендательные технологии", "является оператором по обработке", "участником системы обязательного",
    "информация о процентных ставках", "центр раскрытия корпоративной", "ул. каланчевская",
]
JUNK_RE = [
    re.compile(r"^[+\-−–]?\d+([.,]\d+)?\s?%$"),  # строка-котировка «+1,39%»
    re.compile(r"^\d{1,4}$"),  # счётчики лайков и просмотров
    re.compile(r"^.{0,3}$"),  # обрывки в 1–3 символа
    re.compile(r"^\d+ комментари\w*$", re.I),
    re.compile(r"^\d+\s+(минут\w*|час\w*|дн\w*|секунд\w*)\s+назад$", re.I),
    re.compile(r"^[\w ‑\-]{2,40}\s•\s.{0,30}назад$", re.I),  # «Рынки сейчас • 22 минуты назад»
    re.compile(r"^.{1,30}\.\.\.$"),  # обрезанные названия в виджетах «ФСК - Росс...»
]
# Блок мусора от START до END включительно (комментарии, форма счёта, подвал сайта)
BLOCK_START = ("автор статьи", "войдите в профиль", "напишите комментарий")
BLOCK_END = "не является индивидуальной инвестиционной рекомендацией"
MAX_BLOCK_LINES = 400


@dataclass
class Article:
    url: str
    title: str
    text: str


def find_urls(text: str) -> list[str]:
    urls = []
    for url in URL_RE.findall(text or ""):
        url = url.rstrip(".,;:!?)]")
        if url not in urls:
            urls.append(url)
    return urls


def _is_junk(line: str) -> bool:
    low = line.lower()
    if low in JUNK_EXACT:
        return True
    if any(part in low for part in JUNK_CONTAINS):
        return True
    return any(rx.match(line) for rx in JUNK_RE)


def _is_widget_name(lines: list[str], idx: int) -> bool:
    """Название бумаги в виджете котировок: короткая строка, за которой идёт строка с процентом."""
    line = lines[idx]
    if len(line) > 25 or len(line.split()) > 3 or line.endswith((".", ":", "!", "?")):
        return False
    for nxt in lines[idx + 1:idx + 3]:
        if nxt:
            return bool(JUNK_RE[0].match(nxt))
    return False


def clean_text(raw: str) -> str:
    """Убирает мусор со скопированной страницы и повторы абзацев. Сам текст статей не меняет."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").replace(" ", " ")
    lines = [line.strip() for line in text.split("\n")]

    # 1. Вырезаем блоки «автор — комментарии — форма — подвал»
    kept: list[str] = []
    i = 0
    while i < len(lines):
        low = lines[i].lower()
        if low in BLOCK_START:
            end = next((j for j in range(i + 1, min(len(lines), i + MAX_BLOCK_LINES))
                        if lines[j].lower().startswith(BLOCK_END)), None)
            if end is not None:
                i = end + 1
                continue
            # Конца блока нет (страницу скопировали не до конца): убираем только подпись автора
            i += 1
            while i < len(lines) and lines[i] and len(lines[i].split()) <= 3 and len(lines[i]) < 40:
                i += 1
            continue
        kept.append(lines[i])
        i += 1

    # 2. Убираем мусорные строки и названия из виджетов котировок
    filtered = [line for idx, line in enumerate(kept)
                if not line or not (_is_junk(line) or _is_widget_name(kept, idx))]

    # 3. Убираем повторы длинных абзацев (страницу часто копируют дважды) и лишние пустые строки
    seen: set[str] = set()
    result: list[str] = []
    for line in filtered:
        key = " ".join(line.lower().split())
        if len(key) >= 40:
            if key in seen:
                continue
            seen.add(key)
        if line == "•":
            continue
        if not line and (not result or not result[-1]):
            continue
        result.append(line)
    return "\n".join(result).strip()


def title_of(text: str, limit: int = 140) -> str:
    for line in text.split("\n"):
        line = line.strip(" •-—*")
        if len(line) >= 3:
            return line if len(line) <= limit else line[:limit].rstrip() + "…"
    return "без заголовка"


def extract_article(html_bytes: bytes, url: str) -> Article:
    """HTML страницы -> заголовок и текст статьи. Сначала trafilatura, без неё — простой разбор."""
    title, text = "", ""
    try:
        import trafilatura

        html_text = trafilatura.utils.decode_file(html_bytes) if hasattr(trafilatura, "utils") else html_bytes
        text = trafilatura.extract(html_text, url=url, include_comments=False, include_tables=True,
                                   favor_precision=True) or ""
        meta = trafilatura.extract_metadata(html_text)
        title = (meta.title if meta and meta.title else "") or ""
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - при сбое библиотеки пробуем простой разбор
        text = ""
    if len(text) < 200:
        fallback_title, fallback_text = _simple_extract(html_bytes)
        if len(fallback_text) > len(text):
            text = fallback_text
        title = title or fallback_title
    text = clean_text(text)
    if not text:
        raise ValueError("не удалось достать текст статьи (страница пустая или собирается скриптами)")
    return Article(url=url, title=title.strip() or title_of(text), text=text)


def _simple_extract(html_bytes: bytes) -> tuple[str, str]:
    """Запасной разбор через BeautifulSoup: заголовок и текст из <article>, <main> или <body>."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_bytes, "html.parser")
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = og["content"]
    elif soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "aside", "svg", "iframe"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = []
    for node in root.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        piece = node.get_text(" ", strip=True)
        if piece:
            blocks.append(("• " if node.name == "li" else "") + piece)
    text = "\n".join(blocks) if blocks else root.get_text("\n", strip=True)
    return title, text


async def fetch_article(http, url: str) -> Article:
    """Скачивает страницу по ссылке и достаёт текст. Ссылки на посты Telegram читаются через t.me/s."""
    from telegram_channels import POST_LINK_RE, fetch_single_post

    if POST_LINK_RE.match(url):
        post = await fetch_single_post(http, url)
        if post is None:
            raise ValueError("пост не найден: канал закрыт или пост удалён")
        return Article(url=url, title=title_of(post.text), text=post.text)
    return extract_article(await http.get_bytes(url), url)
