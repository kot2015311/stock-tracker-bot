import asyncio
import unittest
from datetime import datetime

from helpers import FakeHttp, channel_html

from config import MSK
from telegram_channels import fetch_channel_posts, fetch_single_post, first_line, parse_channel_page, post_to_news

PAGE = channel_html("alfa_investments", [
    (8390, "2026-10-23T06:35:00+00:00",
     '<b>Ход торгов: рынок акций в плюсе перед решением ЦБ</b><br/><br/>'
     '<i class="emoji"><b>📈</b></i> Лучше рынка: РусГидро (+2,2%)<br/>Сбербанк ао (-0,1%). Акции вышли из боковика.'),
    (8391, "2026-10-23T07:10:00+00:00", ""),  # только картинка
    (8392, "2026-10-23T07:40:00+00:00", 'Brent выше <a href="https://example.com">$114</a> за баррель'),
])


class ParseTest(unittest.TestCase):
    def test_posts_text_time_and_links(self):
        posts = parse_channel_page(PAGE, "alfa_investments")
        self.assertEqual([p.post_id for p in posts], [8390, 8392])  # пост без текста пропущен
        first = posts[0]
        self.assertEqual(first.published, datetime(2026, 10, 23, 9, 35, tzinfo=MSK))
        self.assertTrue(first.text.startswith("Ход торгов: рынок акций в плюсе перед решением ЦБ\n\n📈 Лучше рынка"))
        self.assertIn("\nСбербанк ао (-0,1%)", first.text)
        self.assertEqual(posts[1].text, "Brent выше $114 за баррель")
        self.assertEqual(first.link, "https://t.me/alfa_investments/8390")

    def test_reply_preview_is_not_taken_as_text(self):
        html = channel_html("ch", [(5, "2026-10-23T07:40:00+00:00", "Основной текст")]).replace(
            '<div class="tgme_widget_message_bubble">',
            '<div class="tgme_widget_message_bubble"><a class="tgme_widget_message_reply">'
            '<div class="tgme_widget_message_text">Цитата</div></a>')
        self.assertEqual(parse_channel_page(html, "ch")[0].text, "Основной текст")

    def test_not_a_channel(self):
        with self.assertRaises(ValueError):
            parse_channel_page("<html><body>Telegram: Contact @x</body></html>", "x")

    def test_post_to_news(self):
        post = parse_channel_page(PAGE, "alfa_investments")[0]
        item = post_to_news(post, "Альфа-Инвестиции", "analytics")
        self.assertEqual(item.uid, "tg:alfa_investments/8390")
        self.assertEqual(item.title, "Ход торгов: рынок акций в плюсе перед решением ЦБ")
        self.assertEqual(item.kind, "analytics")

    def test_first_line_skips_bullets(self):
        self.assertEqual(first_line("•\n— Заголовок новости\nтекст"), "Заголовок новости")


class FetchTest(unittest.TestCase):
    NOW = datetime(2026, 10, 23, 12, 0, tzinfo=MSK)

    def page(self, numbers):
        return channel_html("mt", [(n, "2026-10-23T08:00:00+00:00", f"Новость {n}") for n in numbers])

    def test_first_run_reads_one_page(self):
        http = FakeHttp({"t.me/s/mt": self.page(range(100, 120))})
        posts = asyncio.run(fetch_channel_posts(http, "mt", None, now=self.NOW))
        self.assertEqual(len(posts), 20)
        self.assertEqual(len(http.calls), 1)

    def test_pages_back_until_known_post(self):
        http = FakeHttp({
            "t.me/s/mt": self.page(range(140, 160)),
            "t.me/s/mt?before=140": self.page(range(120, 140)),
            "t.me/s/mt?before=120": self.page(range(100, 120)),
        })
        posts = asyncio.run(fetch_channel_posts(http, "mt", 125, now=self.NOW))
        self.assertEqual([p.post_id for p in posts], list(range(126, 160)))
        self.assertEqual(len(http.calls), 2)  # третья страница не нужна

    def test_old_posts_are_dropped(self):
        old = channel_html("mt", [(1, "2026-10-01T08:00:00+00:00", "Старое"), (2, "2026-10-23T08:00:00+00:00", "Новое")])
        posts = asyncio.run(fetch_channel_posts(FakeHttp({"t.me/s/mt": old}), "mt", None, now=self.NOW))
        self.assertEqual([p.text for p in posts], ["Новое"])

    def test_single_post_by_link(self):
        page = channel_html("markettwits", [(n, "2026-10-23T08:00:00+00:00", f"Новость {n}") for n in range(100, 111)])
        http = FakeHttp({"t.me/s/markettwits?before=111": page})
        post = asyncio.run(fetch_single_post(http, "https://t.me/markettwits/110"))
        self.assertEqual(post.text, "Новость 110")
        self.assertIsNone(asyncio.run(fetch_single_post(http, "https://example.com/1")))


if __name__ == "__main__":
    unittest.main()
