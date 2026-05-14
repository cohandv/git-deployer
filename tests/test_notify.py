from __future__ import annotations

import time
import unittest

from git_deploy_watcher.notify import (
    TelegramRateLimiter,
    format_git_failure_alert,
    format_start_failure_alert,
    truncate_telegram_message,
)


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


class TestFormatAlerts(unittest.TestCase):
    def test_git_alert_compact_three_lines_max(self) -> None:
        text = format_git_failure_alert(
            repo_name="api",
            branch="main",
            phase="clone",
            exit_code=128,
            err_message="git clone failed (exit 128)",
            stderr="fatal: could not read Username\nPermission denied (publickey).",
            stdout="",
            head_sha="deadbeef1234567890abcdef",
        )
        lines = text.splitlines()
        self.assertLessEqual(len(lines), 3)
        self.assertIn("api", lines[0])
        self.assertIn("main", lines[0])
        self.assertIn("deadbeef1234", lines[0])
        self.assertIn("clone", lines[1].lower())
        self.assertIn("128", lines[1])

    def test_start_alert_includes_short_sha(self) -> None:
        text = format_start_failure_alert(
            repo_name="api",
            branch="main",
            head_sha="abcdef1234567890",
            exit_code=1,
            err_message="start.sh exited with 1",
            stderr="npm ERR! missing script",
            stdout="",
        )
        self.assertIn("api", text)
        self.assertIn("abcdef123456", text)
        self.assertIn("start.sh", text)


if __name__ == "__main__":
    unittest.main()
