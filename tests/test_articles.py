import asyncio
import unittest

from helpers import FIXTURES, FakeHttp, channel_html

from articles import clean_text, extract_article, fetch_article, find_urls, title_of


class CleanTextTest(unittest.TestCase):
    def setUp(self):
        self.text = clean_text((FIXTURES / "copied_pages.txt").read_text(encoding="utf-8"))

    def test_articles_kept(self):
        self.assertTrue(self.text.startswith("Ход торгов: тестовый рынок растёт перед решением регулятора"))
        self.assertIn("Главное событие дня — решение регулятора по ставке.", self.text)
        self.assertIn("В лидерах роста: Пример Энерго (+2,2%)", self.text)
        self.assertIn("Нефть удерживается у максимумов", self.text)
        self.assertIn("Новые идеи: во что инвестировать в тестовом мире", self.text)  # страница без подвала

    def test_junk_removed(self):
        for junk in ("Календарь дивидендов", "комментарий пользователя", "Фамилия", "персональных данных",
                     "Откройте бесплатный счёт", "©", "-0,25%", "Тестовая компания...", "Иван Примеров",
                     "15 минут назад", "картинку можно увеличить", "Софт Пример\n"):
            self.assertNotIn(junk, self.text, junk)

    def test_repeated_paragraph_once(self):
        self.assertEqual(self.text.count("Тестовый абзац о том, что нефть остаётся дорогой"), 1)

    def test_no_double_blank_lines(self):
        self.assertNotIn("\n\n\n", self.text)


class LinksTest(unittest.TestCase):
    def test_find_urls(self):
        text = "Смотри https://alfabank.ru/a/1/, и ещё (https://t.me/x/5). https://alfabank.ru/a/1/"
        self.assertEqual(find_urls(text), ["https://alfabank.ru/a/1/", "https://t.me/x/5"])

    def test_extract_simple_page(self):
        html = ("<html><head><title>Сайт</title><meta property='og:title' content='Рубль укрепился'></head><body>"
                "<nav>Меню Главная Новости</nav><article><h1>Рубль укрепился</h1>"
                "<p>Курс доллара опустился до 81 рубля за неделю на фоне геополитических новостей и продаж экспортёров.</p>"
                "<p>Аналитики ждут ослабления рубля к концу года, если цены на нефть снизятся.</p></article>"
                "<footer>© 2026</footer><script>var x=1</script></body></html>").encode("utf-8")
        article = extract_article(html, "https://example.com/a")
        self.assertEqual(article.title, "Рубль укрепился")
        self.assertIn("Курс доллара опустился до 81 рубля", article.text)
        self.assertNotIn("Меню", article.text)
        self.assertNotIn("var x", article.text)

    def test_empty_page_raises(self):
        with self.assertRaises(ValueError):
            extract_article(b"<html><body><script>render()</script></body></html>", "https://example.com")

    def test_telegram_link_reads_post(self):
        page = channel_html("alfa_investments", [(8399, "2026-10-23T07:40:00+00:00", "Пост про Лукойл<br/>Подробности")])
        http = FakeHttp({"t.me/s/alfa_investments?before=8400": page})
        article = asyncio.run(fetch_article(http, "https://t.me/alfa_investments/8399"))
        self.assertEqual(article.title, "Пост про Лукойл")
        self.assertEqual(article.text, "Пост про Лукойл\nПодробности")

    def test_title_of(self):
        self.assertEqual(title_of("\n• Заголовок\nтекст"), "Заголовок")


if __name__ == "__main__":
    unittest.main()
