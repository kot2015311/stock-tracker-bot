import unittest
from datetime import datetime, timedelta

from helpers import TempDataCase

from config import MSK
from news import NewsItem, compile_words, excerpt, load_keywords, name_stem, parse_moex_news, parse_rss, select_news

NOW = datetime(2026, 10, 23, 16, 5, tzinfo=MSK)
SINCE = datetime(2026, 10, 23, 10, 5, tzinfo=MSK)

RSS = (
    "﻿"
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<rss version="2.0"><channel><title>x</title>'
    "<item><title>Инфляционные ожидания в сентябре повысились</title>"
    "<link>https://www.cbr.ru/press/event/?id=1</link><pubDate>Fri, 23 Oct 2026 12:46:00 +0300</pubDate>"
    "<description>&lt;p&gt;Подробности &lt;b&gt;в пресс-релизе&lt;/b&gt;&lt;/p&gt;</description></item>"
    "<item><title>Сбербанк получил разрешение</title>"
    "<link>https://www.cbr.ru/press/event/?id=2</link><pubDate>Thu, 22 Oct 2026 10:00:00 +0300</pubDate></item>"
    "</channel></rss>"
).encode("utf-8")


def item(title, minutes_ago=30, kind="feed", text="", source="Лента", uid=None, added=None):
    published = NOW - timedelta(minutes=minutes_ago)
    return NewsItem(source, published, title, f"https://x/{title[:10]}", text=text, kind=kind,
                    uid=uid or f"u:{title}", added=added or published)


class ParseTest(unittest.TestCase):
    def test_rss_with_bom_and_description(self):
        items = parse_rss(RSS, "ЦБ")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].text, "Подробности в пресс-релизе")
        self.assertEqual(items[0].published, datetime(2026, 10, 23, 12, 46, tzinfo=MSK))

    def test_rss_windows_1251(self):
        body = ('<?xml version="1.0" encoding="windows-1251"?><rss><channel>'
                "<item><title>Лукойл увеличил добычу</title><link>https://x/1</link>"
                "<pubDate>Fri, 23 Oct 2026 12:00:00 +0300</pubDate></item></channel></rss>").encode("cp1251")
        self.assertEqual(parse_rss(body, "Интерфакс")[0].title, "Лукойл увеличил добычу")

    def test_moex_news(self):
        data = {"sitenews": {"columns": ["id", "title", "published_at"],
                             "data": [[101, "О торгах &quot;Тест&quot;", "2026-10-16 21:13:48"]]}}
        news = parse_moex_news(data)
        self.assertIn('"Тест"', news[0].title)
        self.assertEqual(news[0].link, "https://www.moex.com/n101")


class KeywordsTest(TempDataCase):
    def test_word_forms_and_spellings(self):
        sber = compile_words(["re:(?<!\\w)сбер(?!инвест)", "sber"])
        self.assertTrue(sber.search("акции сбербанка выросли"))
        self.assertFalse(sber.search("канал сберинвестиции пишет"))
        x5 = compile_words(["x5", "х5", "пятерочк"])
        self.assertTrue(x5.search("выручка х5 выросла"))
        self.assertTrue(x5.search("выручка x5 выросла"))
        self.assertTrue(x5.search("магазины пятерочка"))

    def test_ozon_and_ozon_pharm_are_different(self):
        keywords, _ = load_keywords({"OZON": {}, "OZPH": {}})
        ozon, ozph = compile_words(keywords["OZON"]), compile_words(keywords["OZPH"])
        self.assertTrue(ozon.search("озон отчитался о росте"))
        self.assertFalse(ozon.search("озон фармацевтика выплатит дивиденды"))
        self.assertTrue(ozph.search("озон фармацевтика выплатит дивиденды"))

    def test_unknown_ticker_uses_name(self):
        keywords, macro = load_keywords({"TATNP": {"name": "Татнефть - привилегированные акции"}})
        self.assertIn("татнефт", keywords["TATNP"])
        self.assertIn("ключевую ставк", macro)
        self.assertEqual(name_stem("Озон"), "озон")

    def test_macro_gas_does_not_match_newspaper(self):
        _, macro = load_keywords({})
        pattern = compile_words(macro)
        self.assertTrue(pattern.search("цены на газ в европе выросли"))
        self.assertFalse(pattern.search("газета вышла утром"))

    def test_excerpt_takes_matching_paragraph(self):
        text = "Ход торгов\n" + "Вводный абзац про рынок. " * 30 + "\nСбербанк ао (-0,1%). Акции вышли из боковика."
        news = NewsItem("Альфа", NOW, "Ход торгов", "", text=text, kind="analytics")
        self.assertTrue(excerpt(news, compile_words(["сбер"])).startswith("Сбербанк ао (-0,1%)"))


class SelectTest(TempDataCase):
    def test_sections(self):
        keywords, macro = load_keywords({"SBER": {}, "LKOH": {}})
        items = [
            item("Сбербанк отчитался по МСФО", 60),
            item("Сбербанк отчитался по МСФО", 50, source="Другая лента", uid="dup"),  # дубль заголовка
            item("Лукойл: дивиденды", 60 * 24 * 5),  # старше 3 дней
            item("Ход торгов", 120, kind="analytics",
                 text="Рынок растёт. " * 40 + "\nЛукойл (+1%) лучше рынка.\nЕщё абзац."),
            item("Утренний обзор", 60 * 10, kind="analytics", text="Вчерашний обзор"),  # до прошлого отчёта
            item("ЦБ сохранил ключевую ставку 14%", 150),
            item("Футбол: результаты тура", 40),
            item("Моя заметка", 30, kind="manual", text="Текст заметки"),
            item("Старая заметка", 60 * 20, kind="manual"),
        ]
        sel = select_news(items, keywords, macro, NOW, SINCE)
        self.assertEqual([m.item.title for m in sel.by_ticker["SBER"]], ["Сбербанк отчитался по МСФО"])
        self.assertEqual(sel.by_ticker["LKOH"][0].excerpt, "Лукойл (+1%) лучше рынка.")
        self.assertTrue(sel.by_ticker["LKOH"][0].is_new)
        self.assertEqual([i.title for i in sel.analytics], ["Ход торгов"])
        self.assertEqual([m.item.title for m in sel.macro], ["ЦБ сохранил ключевую ставку 14%"])
        self.assertEqual([i.title for i in sel.manual_new], ["Моя заметка"])
        self.assertEqual([i.title for i in sel.manual_old], ["Старая заметка"])

    def test_first_report_uses_last_day(self):
        keywords, macro = load_keywords({"SBER": {}})
        items = [item("Обзор", 60 * 20, kind="analytics", text="Сбербанк вырос на 1%"),
                 item("Обзор позавчера", 60 * 30, kind="analytics", text="Сбербанк упал на 1%")]
        sel = select_news(items, keywords, macro, NOW, None)
        self.assertEqual([i.title for i in sel.analytics], ["Обзор"])

    def test_similar_titles_from_two_feeds_once(self):
        keywords, macro = load_keywords({"SBER": {}})
        items = [item("Сбербанк в 2026 году заработает около 2 трлн рублей чистой прибыли", 10, uid="a"),
                 item("Сбербанк в 2026 году получит около 2 трлн рублей чистой прибыли", 11, uid="b")]
        sel = select_news(items, keywords, macro, NOW, SINCE)
        self.assertEqual(len(sel.by_ticker["SBER"]), 1)

    def test_polls_and_pinned_posts_are_not_reviews(self):
        keywords, macro = load_keywords({"SBER": {}})
        items = [item("Канал pinned a photo", 10, kind="analytics", text="Канал pinned a photo", uid="p"),
                 item("Опрос", 20, kind="analytics", text="Ставите на флоутеры? 🔥 — да", uid="q"),
                 item("Длинный разбор", 30, kind="analytics", text="Разбор металлургов: акции Северстали и ММК, дивиденды. " * 10, uid="r"),
                 item("Покемоны", 40, kind="analytics", uid="m", text=("Пенсионер заработал миллион на карточках. " * 12)
                      + "Есть коллекция потраченных на рынке нервных клеток")]
        sel = select_news(items, keywords, macro, NOW, SINCE)
        self.assertEqual([i.title for i in sel.analytics], ["Длинный разбор"])

    def test_macro_ignores_prices_in_rubles(self):
        _, macro = load_keywords({})
        pattern = compile_words(macro)
        self.assertFalse(pattern.search("стоимость артефактов составила около 1 млн рублей"))
        self.assertFalse(pattern.search("самозапреты на ставки не являются методом борьбы с казино"))
        self.assertTrue(pattern.search("банк россии сохранил ключевую ставку"))
        self.assertTrue(pattern.search("курс рубля к юаню укрепился"))

    def test_hashtag_line_is_not_the_title(self):
        from telegram_channels import first_line

        text = "🇷🇺#SBER\nСбербанк в сентябре заработал 160 млрд рублей по РСБУ"
        self.assertEqual(first_line(text), "Сбербанк в сентябре заработал 160 млрд рублей по РСБУ")
        news_item = NewsItem("MarketTwits", NOW, "x", "", text=text)
        self.assertTrue(excerpt(news_item, compile_words(["sber"])).startswith("Сбербанк в сентябре"))
        _, macro = load_keywords({})
        self.assertFalse(compile_words(macro).search("ключевыми вопросами повестки станут"))

    def test_moex_service_news_is_noise(self):
        keywords, macro = load_keywords({"SBER": {}})
        noise = NewsItem("Мосбиржа", NOW, "Изменены значения ставки переноса РЕПО ценной бумаги SBER", "", uid="m1")
        sel = select_news([noise], keywords, macro, NOW, SINCE)
        self.assertEqual(sel.by_ticker, {})
        self.assertEqual(sel.macro, [])


if __name__ == "__main__":
    unittest.main()
