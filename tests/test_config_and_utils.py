import os
import tempfile
import unittest
from datetime import time
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401

import config
from http_client import describe_error
from utils import chunks, parse_number


class ConfigTest(unittest.TestCase):
    BASE = {"BOT_TOKEN": "1:x", "TINKOFF_TOKEN": "t", "OWNER_ID": "42"}

    def test_parse_times(self):
        self.assertEqual(config.parse_times("16:05, 10:05;19:05"), [time(10, 5), time(16, 5), time(19, 5)])
        self.assertEqual(config.parse_times("off"), [])
        with self.assertRaises(ValueError):
            config.parse_times("16-05")

    def test_defaults(self):
        with mock.patch.dict(os.environ, self.BASE, clear=True):
            settings = config.load_settings()
        self.assertEqual(settings.report_times, [time(10, 5), time(16, 5), time(19, 5)])
        self.assertEqual(settings.check_interval, 300)
        self.assertTrue(settings.weekdays_only)

    def test_old_report_interval_zero_disables(self):
        with mock.patch.dict(os.environ, {**self.BASE, "REPORT_INTERVAL": "0"}, clear=True):
            self.assertEqual(config.load_settings().report_times, [])

    def test_missing_tokens_stop_with_message(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                config.load_settings()
        self.assertIn("BOT_TOKEN", str(ctx.exception))

    def test_ca_bundle_from_env(self):
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp:
            path = tmp.name
        try:
            with mock.patch.dict(os.environ, {"CA_BUNDLE": path}, clear=True):
                self.assertEqual(config.setup_ca_bundle(), path)
                self.assertEqual(os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"], path)
        finally:
            Path(path).unlink()


class UtilsTest(unittest.TestCase):
    def test_chunks(self):
        text = "\n".join(f"строка {i}" for i in range(1000))
        parts = chunks(text, 500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("\n".join(parts), text)
        self.assertEqual(chunks("a" * 1200, 500), ["a" * 500, "a" * 500, "a" * 200])

    def test_parse_number(self):
        self.assertEqual(parse_number("1 124,5"), 1124.5)
        with self.assertRaises(ValueError):
            parse_number("abc")

    def test_describe_error_hides_bot_token(self):
        text = describe_error(RuntimeError("url https://api.telegram.org/bot123456:AAH-secret_Token/getMe"))
        self.assertNotIn("secret", text)
        self.assertIn("bot***", text)

    def test_describe_timeout(self):
        self.assertIn("таймаут", describe_error(TimeoutError()))


if __name__ == "__main__":
    unittest.main()
