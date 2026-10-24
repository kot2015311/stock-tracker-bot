"""HTTP-клиент для всех открытых источников: Мосбиржа, ЦБ, RSS, Telegram-каналы, статьи.

Общий сеанс, таймауты, одна повторная попытка при сбое сети и понятные тексты ошибок.
В тестах вместо него подставляется подделка с теми же методами get_json, get_bytes, get_text.
"""

from __future__ import annotations

import asyncio
import os
import re
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

# Многие сайты не отдают страницы «неизвестным программам», поэтому представляемся браузером
# и честно добавляем название бота.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 stock-tracker-bot/2.0"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"}
TOTAL_TIMEOUT = 25  # секунд на один запрос
RETRY_DELAY = 1.5  # пауза перед повторной попыткой, секунд
RETRY_STATUSES = {429, 500, 502, 503, 504}  # временные ошибки сервера, их стоит повторить

_TOKEN_RE = re.compile(r"bot\d+:[\w-]+")  # токен бота не должен попасть в логи и отчёты

_STATUS_HINTS = {
    400: "сайт не понял запрос",
    401: "нужна авторизация (проверьте токен)",
    403: "доступ запрещён: сайт не пускает автоматические запросы или ваш IP (VPN?)",
    404: "адрес не найден, источник мог переехать",
    429: "слишком частые запросы, сайт просит подождать",
    500: "ошибка на стороне сайта",
    502: "сайт временно недоступен",
    503: "сайт временно недоступен",
    504: "сайт не успел ответить",
}


def describe_error(exc: BaseException) -> str:
    """Короткое понятное описание ошибки для отчёта и проверки источников."""
    text = _TOKEN_RE.sub("bot***", str(exc).strip().replace("\n", " "))
    try:
        import aiohttp
    except ImportError:
        aiohttp = None  # type: ignore[assignment]

    if aiohttp is not None:
        if isinstance(exc, aiohttp.ClientResponseError):
            hint = _STATUS_HINTS.get(exc.status, exc.message or "ошибка ответа")
            return f"HTTP {exc.status}: {hint}"
        if isinstance(exc, (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError)):
            return "ошибка сертификата: нужен ca_bundle.pem с корнем Минцифры (см. README)"
        if isinstance(exc, aiohttp.ClientConnectorError):
            host = getattr(exc, "host", "") or "сайту"
            return f"нет соединения с {host}: сайт недоступен из вашей сети (проверьте VPN и правила для доменов)"
        if isinstance(exc, aiohttp.ServerDisconnectedError):
            return "сайт оборвал соединение"
        if isinstance(exc, aiohttp.ContentTypeError):
            return "сайт вернул не тот формат данных (часто это страница-заглушка или капча)"
    if isinstance(exc, ssl.SSLError):
        return "ошибка сертификата: нужен ca_bundle.pem с корнем Минцифры (см. README)"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"таймаут: нет ответа за {TOTAL_TIMEOUT} сек"
    return f"{type(exc).__name__}: {text[:160]}" if text else type(exc).__name__


def _ssl_context() -> ssl.SSLContext | None:
    """Свой набор сертификатов, если он найден (см. config.setup_ca_bundle), иначе системный."""
    path = os.getenv("SSL_CA_BUNDLE") or os.getenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH")
    if path and Path(path).is_file():
        try:
            return ssl.create_default_context(cafile=path)
        except (ssl.SSLError, OSError):
            return None
    return None


class Http:
    """Минимальный клиент: get_json, get_bytes, get_text."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def _get(self, url: str, params: dict | None, mode: str) -> Any:
        import aiohttp  # импорт здесь, чтобы тесты работали без aiohttp

        last_exc: Exception | None = None
        for attempt in range(2):  # вторая попытка только при временных сбоях
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status in RETRY_STATUSES and attempt == 0:
                        last_exc = aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status, message=resp.reason or ""
                        )
                        await asyncio.sleep(RETRY_DELAY * 2)
                        continue
                    resp.raise_for_status()
                    if mode == "json":
                        return await resp.json(content_type=None)
                    body = await resp.read()
                    if mode == "bytes":
                        return body
                    return body.decode(resp.charset or "utf-8", errors="replace")
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
        assert last_exc is not None
        raise last_exc

    async def get_json(self, url: str, params: dict | None = None) -> Any:
        return await self._get(url, params, "json")

    async def get_bytes(self, url: str, params: dict | None = None) -> bytes:
        return await self._get(url, params, "bytes")

    async def get_text(self, url: str, params: dict | None = None) -> str:
        return await self._get(url, params, "text")


@asynccontextmanager
async def open_http(timeout: float = TOTAL_TIMEOUT) -> AsyncIterator[Http]:
    """Открывает общий сеанс на время одной задачи (отчёт, сбор новостей, проверка)."""
    import aiohttp

    ctx = _ssl_context()
    connector = aiohttp.TCPConnector(ssl=ctx, limit_per_host=4) if ctx else aiohttp.TCPConnector(limit_per_host=4)
    async with aiohttp.ClientSession(
        headers=HEADERS,
        timeout=aiohttp.ClientTimeout(total=timeout),
        connector=connector,
        # USE_SYSTEM_PROXY=1 в .env: ходить через системный прокси (нужно, если VPN работает как прокси)
        trust_env=os.getenv("USE_SYSTEM_PROXY", "0").strip() == "1",
    ) as session:
        yield Http(session)
