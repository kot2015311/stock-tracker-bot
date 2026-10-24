import asyncio
import json
import unittest
from datetime import date, datetime, timedelta

from helpers import FakeHttp, FakeMarket, TempDataCase, channel_html, make_candles_json

import config
from config import MSK
from market_data import Dividend
from news import NewsItem
from news_store import NewsStore
from portfolio import Position, save_portfolio
from report import build_report, last_report_time, mark_report_sent, save_report_files

NOW = datetime(2026, 10, 23, 16, 5, tzinfo=MSK)
WATCH = {
    "SBER": {"figi": "F_SBER", "name": "Сбербанк", "ref": 290.0, "threshold": 3.0},
    "NVTK": {"figi": "F_NVTK", "name": "НОВАТЭК", "ref": 1140.0, "threshold": 3.0},
    "LKOH": {"figi": "F_LKOH", "name": "ЛУКОЙЛ", "ref": 6000.0, "threshold": 3.0},
}
PRICES = {"F_SBER": 291.31, "F_NVTK": 1151.7, "F_LKOH": 6068.0}
FUTURES = {
    "securities": {"columns": ["SECID", "SHORTNAME", "ASSETCODE", "LASTTRADEDATE", "PREVSETTLEPRICE"],
                   "data": [["BRX6", "BR-11.26", "BR", "2026-11-02", 112.9]]},
    "marketdata": {"columns": ["SECID", "LAST", "OPENPOSITION"], "data": [["BRX6", 113.7, 250000]]},
}
KEY_RATE = "<tr><td>23.10.2026</td><td>14,00</td></tr><tr><td>27.07.2026</td><td>14,00</td></tr><tr><td>25.07.2026</td><td>15,00</td></tr>"
CBR = {"Date": "2026-10-23T11:30:00+03:00", "Valute": {"USD": {"Nominal": 1, "Value": 81.13, "Previous": 81.97}}}
ISS_DIVIDENDS = {"dividends": {"columns": ["secid", "registryclosedate", "value", "currencyid"],
                               "data": [["SBER", "2026-07-20", 37.64, "RUB"]]}}


def routes(**override):
    data = {
        "securities/SBER/candles.json": make_candles_json(260, start=date(2025, 10, 1), base=200, step=0.35),
        "securities/NVTK/candles.json": make_candles_json(260, start=date(2025, 10, 1), base=1000, step=0.6),
        "securities/LKOH/candles.json": make_candles_json(260, start=date(2025, 10, 1), base=5500, step=2),
        "securities/IMOEX/candles.json": make_candles_json(260, start=date(2025, 10, 1), base=2300, step=0.5),
        "securities/RGBI/candles.json": make_candles_json(60, start=date(2026, 8, 1), base=110, step=0.03),
        "securities/SBER/dividends.json": ISS_DIVIDENDS,
        "securities/NVTK/dividends.json": {"dividends": {"columns": [], "data": []}},
        "forts/boards/RFUD": FUTURES,
        "cbr-xml-daily": CBR,
        "hd_base/KeyRate": KEY_RATE,
    }
    data.update(override)
    return data


def market(**kwargs):
    dividends = {
        "F_LKOH": [Dividend("2026-12-09", 535.0, "RUB", last_buy_date="2026-12-08")],
        "F_NVTK": [Dividend("2026-10-12", 35.5, "RUB", last_buy_date="2026-10-09")],
        "F_SBER": RuntimeError("Т-API недоступен"),
    }
    return FakeMarket(kwargs.get("prices", PRICES), dividends, kwargs.get("fail"))


class ReportTest(TempDataCase):
    def setUp(self):
        super().setUp()
        save_portfolio({"NVTK": Position("NVTK", -5, 1124, -104), "LKOH": Position("LKOH", 2, 5900)})

    def build(self, store=None, http=None, mkt=None, since=None, now=NOW):
        return asyncio.run(build_report(mkt or market(), WATCH, store=store, http=http or FakeHttp(routes()),
                                        now=now, since=since, collect=False))

    def test_full_report(self):
        text = self.build().text
        self.assertIn("ОТЧЁТ НА 2026-10-23 16:05 (МСК)", text)
        self.assertIn("NVTK | -5 (шорт) | 1124.00 | 1151.70 | -138.50 ₽ (-2.5%)", text)
        self.assertIn("LKOH | 2 (лонг) | 5900.00 | 6068.00 | +336.00 ₽", text)
        self.assertIn("Зафиксировано по закрытым частям: -104.00 ₽", text)
        self.assertIn("Нефть Brent (фьючерс BRX6 на Мосбирже, до 2026-11-02): $113.70", text)
        self.assertIn("Ключевая ставка ЦБ: 14.00% с 27.07.2026 (до этого 15.00%)", text)
        self.assertIn("USD 81.13", text)
        self.assertIn("RSI14", text)
        self.assertIn("ТЕХНИЧЕСКАЯ КАРТИНА", text)
        self.assertIn("LKOH: 535 RUB, отсечка 2026-12-09, купить до 2026-12-08 (через 46 дн.); на ваши 2 шт ≈ 1 070 ₽", text)
        self.assertIn("SBER: 2026-07-20: 37.64 RUB", text)  # Т-API упал, взяли ISS
        self.assertNotIn("ПРОБЛЕМЫ ИСТОЧНИКОВ", text)
        self.assertIn("Это данные, а не инвестиционная рекомендация.", text)

    def test_second_report_shows_change_since_previous(self):
        self.build()
        text = self.build(mkt=market(prices={**PRICES, "F_SBER": 294.22})).text
        self.assertIn("SBER | 294.22 ₽ | +1.00%", text)

    def test_broken_sources_are_listed_but_report_is_built(self):
        http = FakeHttp(routes(**{"forts/boards/RFUD": TimeoutError(), "hd_base/KeyRate": ValueError("капча"),
                                  "securities/NVTK/candles.json": ConnectionError("сброс")}))
        text = self.build(http=http).text
        self.assertIn("ПРОБЛЕМЫ ИСТОЧНИКОВ", text)
        self.assertIn("Мосбиржа (фьючерс Brent): таймаут", text)
        self.assertIn("ЦБ РФ (ключевая ставка)", text)
        self.assertIn("NVTK | 1151.70 ₽ | первый отчёт | нет данных ISS о динамике", text)

    def test_price_fallback_to_iss_when_tinkoff_fails(self):
        text = self.build(mkt=market(fail=RuntimeError("UNAVAILABLE"))).text
        self.assertIn("Т-Инвестиции (цены)", text)
        self.assertIn("* цена закрытия из ISS", text)
        self.assertIn("NVTK | -5 (шорт)", text)  # результат посчитан по цене ISS

    def test_news_sections(self):
        store = NewsStore()
        since = NOW - timedelta(hours=6)
        store.add([
            NewsItem("MarketTwits", NOW - timedelta(hours=1), "Новатэк запустил новую линию СПГ", "https://t.me/mt/1", uid="a"),
            NewsItem("Альфа-Инвестиции", NOW - timedelta(hours=2), "Ход торгов", "https://t.me/alfa/2",
                     text="Ход торгов\nЛукойл (+1%) лучше рынка на дорогой нефти.", kind="analytics", uid="b"),
            NewsItem("Интерфакс", NOW - timedelta(hours=3), "Банк России сохранил ключевую ставку 14%", "", uid="c"),
        ], now=NOW)
        store.add_manual("Моя статья", "Полный текст моей статьи", link="https://alfabank.ru/x", now=NOW - timedelta(hours=1))
        text = self.build(store=store, since=since).text
        self.assertIn("Прошлый отчёт: 2026-10-23 10:05", text)
        self.assertIn("NVTK\n- [новое] 2026-10-23 15:05 [MarketTwits] Новатэк запустил новую линию СПГ", text)
        self.assertIn("=== 2026-10-23 14:05 | Альфа-Инвестиции | https://t.me/alfa/2\nХод торгов", text)
        self.assertIn("- 2026-10-23 13:05 [Интерфакс] Банк России сохранил ключевую ставку 14%", text)
        self.assertIn("Полный текст моей статьи", text)

    def test_collect_runs_with_report(self):
        sources_file = self.data_dir / "sources.json"
        sources_file.write_text(json.dumps({"telegram": [{"channel": "mt", "name": "MarketTwits"}], "rss": []}),
                                encoding="utf-8")
        saved = config.SOURCES_FILE
        config.SOURCES_FILE = sources_file
        try:
            page = channel_html("mt", [(1, "2026-10-23T12:00:00+00:00", "Лукойл увеличил добычу")])
            store = NewsStore()
            report = asyncio.run(build_report(market(), WATCH, store=store, http=FakeHttp(routes(**{"t.me/s/mt": page})),
                                              now=NOW, since=None))
        finally:
            config.SOURCES_FILE = saved
        self.assertIn("[MarketTwits] Лукойл увеличил добычу", report.text)

    def test_state_and_files(self):
        report = self.build()
        save_report_files(report)
        mark_report_sent(report)
        self.assertEqual(last_report_time(), NOW)
        self.assertTrue((config.REPORTS_DIR / "report_20261023_1605.txt").exists())
        data = json.loads((config.REPORTS_DIR / "report_20261023_1605.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["prices"]["NVTK"], 1151.7)
        self.assertEqual(data["key_rate"], 14.0)


if __name__ == "__main__":
    unittest.main()
