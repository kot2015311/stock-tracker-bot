"""API Т-Инвестиций: поиск акции по тикеру, текущие цены, дивиденды.

SDK синхронный, поэтому каждый запрос выполняется в отдельном потоке (asyncio.to_thread),
чтобы бот не «зависал». SDK импортируется внутри функций: так тесты и проверка
источников работают даже без него, а сертификаты успевают настроиться до импорта gRPC.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from market_data import Dividend, parse_tinkoff_dividends


def _money(value) -> float:
    """Цена в API хранится как units (рубли) + nano (миллиардные доли). Складываем в обычное число."""
    return value.units + value.nano / 1e9


class TinkoffMarket:
    def __init__(self, token: str) -> None:
        self._token = token

    # ---------------------------------------------------------------- синхронные вызовы SDK
    def _find_share_sync(self, ticker: str) -> dict | None:
        from t_tech.invest import Client  # официальный SDK Т-Банка (пакет t-tech-investments)

        with Client(self._token) as client:
            found = client.instruments.find_instrument(query=ticker)
            for item in found.instruments:
                # Только акции основного режима торгов Мосбиржи с точным совпадением тикера
                if (item.instrument_type == "share"
                        and item.ticker.upper() == ticker.upper()
                        and item.class_code == "TQBR"):
                    return {"figi": item.figi, "name": item.name, "ticker": item.ticker.upper()}
        return None

    def _prices_sync(self, figis: list[str]) -> dict[str, float]:
        from t_tech.invest import Client

        with Client(self._token) as client:
            response = client.market_data.get_last_prices(figi=figis)
            return {item.figi: _money(item.price) for item in response.last_prices}

    def _dividends_sync(self, figi: str, now: datetime) -> list[Dividend]:
        from t_tech.invest import Client

        with Client(self._token) as client:
            response = client.instruments.get_dividends(
                figi=figi,
                from_=now - timedelta(days=730),
                to=now + timedelta(days=365),
            )
        return parse_tinkoff_dividends(response.dividends)

    # ---------------------------------------------------------------- асинхронные обёртки
    async def find_share(self, ticker: str) -> dict | None:
        return await asyncio.to_thread(self._find_share_sync, ticker)

    async def prices(self, figis: list[str]) -> dict[str, float]:
        if not figis:  # пустой список API не любит — сразу выходим
            return {}
        return await asyncio.to_thread(self._prices_sync, list(dict.fromkeys(figis)))

    async def dividends(self, figi: str, now: datetime | None = None) -> list[Dividend]:
        moment = now or datetime.now(timezone.utc)
        return await asyncio.to_thread(self._dividends_sync, figi, moment)
