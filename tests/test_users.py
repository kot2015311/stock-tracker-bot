import unittest

from helpers import TempDataCase

import config
import users
from news_store import NewsStore
from portfolio import Position, load_portfolio, save_portfolio


class UsersTest(TempDataCase):
    def setUp(self):
        super().setUp()
        self._owner = config.owner_id
        config.owner_id = 1

    def tearDown(self):
        config.owner_id = self._owner
        super().tearDown()

    def as_user(self, uid):
        return config.current_user.set(uid)

    def test_access_list(self):
        self.assertTrue(users.is_allowed(1))
        self.assertFalse(users.is_allowed(2))
        self.assertTrue(users.add_user(2))
        self.assertFalse(users.add_user(2))
        self.assertTrue(users.is_allowed(2))
        self.assertEqual(users.all_users(), [1, 2])
        self.assertTrue(users.remove_user(2))
        self.assertFalse(users.is_allowed(2))

    def test_each_user_has_own_portfolio(self):
        token = self.as_user(1)
        save_portfolio({"SBER": Position("SBER", 6, 283)})
        config.current_user.reset(token)
        token = self.as_user(2)
        self.assertEqual(load_portfolio(), {})
        save_portfolio({"LKOH": Position("LKOH", 1, 6000)})
        self.assertTrue((self.data_dir / "users" / "2" / "portfolio.json").exists())
        config.current_user.reset(token)
        token = self.as_user(1)
        self.assertEqual(list(load_portfolio()), ["SBER"])
        config.current_user.reset(token)

    def test_manual_materials_are_private(self):
        store = NewsStore()
        token = self.as_user(1)
        store.add_manual("Моя статья", "текст владельца")
        config.current_user.reset(token)
        token = self.as_user(2)
        self.assertEqual(store.manual(), [])
        store.add_manual("Чужая статья", "текст второго")
        self.assertEqual([i.title for i in store.manual()], ["Чужая статья"])
        self.assertEqual(store.clear_manual(), 1)
        config.current_user.reset(token)
        token = self.as_user(1)
        self.assertEqual([i.title for i in store.manual()], ["Моя статья"])
        config.current_user.reset(token)


if __name__ == "__main__":
    unittest.main()
