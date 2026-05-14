from __future__ import annotations

import time
import unittest

from git_deploy_watcher.notify import TelegramRateLimiter, truncate_telegram_message


class TestTruncate(unittest.TestCase):
    def test_short_unchanged(self) -> None:
        s = "hello"
        self.assertEqual(truncate_telegram_message(s, max_len=100), s)

    def test_truncates_with_suffix(self) -> None:
        s = "x" * 5000
        out = truncate_telegram_message(s, max_len=100)
        self.assertLessEqual(len(out), 100)
        self.assertTrue(out.endswith("…(truncated)"))


class TestRateLimiter(unittest.TestCase):
    def test_blocks_within_window(self) -> None:
        lim = TelegramRateLimiter(window_seconds=10.0)
        self.assertTrue(lim.allow("a"))
        self.assertFalse(lim.allow("a"))
        self.assertTrue(lim.allow("b"))

    def test_allows_after_window(self) -> None:
        lim = TelegramRateLimiter(window_seconds=0.05)
        self.assertTrue(lim.allow("x"))
        self.assertFalse(lim.allow("x"))
        time.sleep(0.06)
        self.assertTrue(lim.allow("x"))


if __name__ == "__main__":
    unittest.main()
