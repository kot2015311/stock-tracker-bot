"""Настройки бота: пути к файлам и параметры из файла .env.

Все модули берут пути отсюда. Пути считаются от папки проекта, поэтому бот
работает, даже если запустить его из другой папки.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"  # настройки источников (лежат в git)
DATA_DIR = BASE_DIR / "data"  # ваши личные данные (в git не попадают)

SOURCES_FILE = CONFIG_DIR / "sources.json"
KEYWORDS_FILE = CONFIG_DIR / "keywords.json"

NEWS_FILE = DATA_DIR / "news_store.json"  # новости общие для всех пользователей
USERS_FILE = DATA_DIR / "users.json"  # кому владелец открыл доступ

# Личные файлы каждого пользователя. Владелец хранит их прямо в data/ (как раньше),
# остальные — в data/users/<telegram id>/. Путь выбирается по текущему пользователю.
_USER_FILES = {
    "WATCH_FILE": "watchlist.json",
    "PORTFOLIO_FILE": "portfolio.json",
    "TRADES_FILE": "trades.json",
    "ALERTS_FILE": "alerts.json",
    "STATE_FILE": "state.json",
    "LAST_PRICES_FILE": "last_prices.json",
    "PLAN_FILE": "plan.json",
    "REPORTS_DIR": "reports",
}
current_user: ContextVar[int | None] = ContextVar("current_user", default=None)
owner_id: int | None = None  # задаётся при запуске бота


def user_dir(user_id: int | None = None) -> Path:
    uid = user_id if user_id is not None else current_user.get()
    if uid is None or uid == owner_id:
        return DATA_DIR
    return DATA_DIR / "users" / str(uid)


def __getattr__(name: str) -> Path:
    if name in _USER_FILES:
        return user_dir() / _USER_FILES[name]
    raise AttributeError(name)

MSK = timezone(timedelta(hours=3))  # Москва, без перехода на летнее время

DEFAULT_REPORT_TIMES = "10:05,16:05,19:05"


def load_env() -> None:
    """Читает .env из папки проекта. Переменные, уже заданные в системе, не перезаписываются."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # без python-dotenv работают только переменные окружения
        return
    load_dotenv(BASE_DIR / ".env")


def setup_ca_bundle() -> str | None:
    """Находит файл сертификатов и сообщает о нём gRPC (Т-Инвестиции) и HTTP-клиенту.

    Файл нужен, если ваша сеть или российские сайты требуют сертификат Минцифры.
    Порядок поиска: CA_BUNDLE из .env, старые переменные, ca_bundle.pem в папке проекта
    и рядом с ней (например, на рабочем столе). Вызывать до первого импорта SDK.
    """
    candidates = [
        os.getenv("CA_BUNDLE"),
        os.getenv("SSL_CA_BUNDLE"),
        os.getenv("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"),
        str(BASE_DIR / "ca_bundle.pem"),
        str(BASE_DIR.parent / "ca_bundle.pem"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = path
            os.environ["SSL_CA_BUNDLE"] = path
            return path
    return None


def parse_times(raw: str) -> list[time]:
    """'10:05, 16:05' -> [10:05, 16:05]. Пустая строка, 0 или off выключают расписание."""
    raw = (raw or "").strip().lower()
    if raw in {"", "0", "off", "нет"}:
        return []
    result: set[time] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hours, minutes = part.split(":")
            result.add(time(int(hours), int(minutes)))
        except ValueError as exc:
            raise ValueError(f"не понял время «{part}», нужен формат ЧЧ:ММ, например 16:05") from exc
    return sorted(result)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"❌ В .env {name}={raw}: нужно целое число") from exc


@dataclass
class Settings:
    bot_token: str
    tinkoff_token: str
    owner_id: int
    check_interval: int = 300  # как часто проверять цены и уровни, сек
    news_interval: int = 600  # как часто собирать новости, сек
    report_times: list[time] = field(default_factory=list)
    weekdays_only: bool = True  # отчёты по расписанию только по будням


def load_settings() -> Settings:
    """Читает и проверяет настройки. При ошибке останавливает бота с понятным текстом."""
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    tinkoff_token = os.getenv("TINKOFF_TOKEN", "").strip()
    owner_raw = os.getenv("OWNER_ID", "").strip()
    if not bot_token or not tinkoff_token or not owner_raw.isdigit():
        raise SystemExit("❌ Заполните BOT_TOKEN, TINKOFF_TOKEN и OWNER_ID в файле .env (образец: .env.example)")

    raw_times = os.getenv("REPORT_TIMES")
    if raw_times is None:
        # Старая настройка: REPORT_INTERVAL=0 выключала автоотчёт. Уважаем её.
        raw_times = "" if os.getenv("REPORT_INTERVAL", "").strip() == "0" else DEFAULT_REPORT_TIMES
    try:
        report_times = parse_times(raw_times)
    except ValueError as exc:
        raise SystemExit(f"❌ В .env REPORT_TIMES: {exc}") from exc

    weekdays_raw = os.getenv("REPORT_WEEKDAYS_ONLY", os.getenv("REPORT_TRADING_HOURS", "1")).strip()
    return Settings(
        bot_token=bot_token,
        tinkoff_token=tinkoff_token,
        owner_id=int(owner_raw),
        check_interval=max(60, _int_env("CHECK_INTERVAL", 300)),
        news_interval=max(120, _int_env("NEWS_INTERVAL", 600)),
        report_times=report_times,
        weekdays_only=weekdays_raw != "0",
    )
