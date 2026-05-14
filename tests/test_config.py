from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from git_deploy_watcher.config import (
    AppConfig,
    ConfigError,
    TelegramConfig,
    build_git_env,
    load_config,
    telegram_credentials,
)


def _write_config(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


class TestLoadConfig(unittest.TestCase):
    def test_start_sh_retry_defaults(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.start_sh_failure_retry_attempts, 5)
            self.assertEqual(cfg.start_sh_failure_retry_interval_seconds, 10)
            self.assertEqual(cfg.deploy_backoff_initial_seconds, 10)
            self.assertEqual(cfg.deploy_backoff_max_seconds, 300)
            self.assertEqual(cfg.start_sh_timeout_seconds, 300)

    def test_start_sh_retry_custom(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "start_sh_failure_retry_attempts": 12,
                    "start_sh_failure_retry_interval_seconds": 0,
                    "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.start_sh_failure_retry_attempts, 12)
            self.assertEqual(cfg.start_sh_failure_retry_interval_seconds, 0)

    def test_deploy_backoff_rejects_max_below_initial(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "deploy_backoff_initial_seconds": 60,
                    "deploy_backoff_max_seconds": 30,
                    "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
                },
            )
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_accepts_scp_style_ssh(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.repos[0].name, "foo")
            self.assertEqual(cfg.repos[0].url, "git@github.com:org/foo.git")

    def test_accepts_ssh_url_form(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "repos": [
                        {
                            "name": "bar",
                            "url": "ssh://git@gitlab.com/group/bar.git",
                            "branch": "develop",
                        }
                    ],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.repos[0].name, "bar")

    def test_rejects_https(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "repos": [{"url": "https://github.com/org/foo.git", "branch": "main"}],
                },
            )
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_duplicate_names(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "repos": [
                        {"name": "x", "url": "git@h:a/x.git", "branch": "main"},
                        {"name": "x", "url": "git@h:a/y.git", "branch": "main"},
                    ],
                },
            )
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_telegram_numeric_chat_id_in_chat_id_env_migrated(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "telegram": {"chat_id_env": "1380628864"},
                    "repos": [{"url": "git@h:a/x.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.telegram.chat_id, "1380628864")
            self.assertEqual(cfg.telegram.chat_id_env, "TELEGRAM_CHAT_ID")

    def test_telegram_bot_token_in_bot_token_env_migrated(self) -> None:
        token = "1234567890123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "telegram": {"bot_token_env": token},
                    "repos": [{"url": "git@h:a/x.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.telegram.bot_token, token)
            self.assertEqual(cfg.telegram.bot_token_env, "TELEGRAM_BOT_TOKEN")

    def test_telegram_chat_id_env_invalid_non_numeric_still_errors(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "telegram": {"chat_id_env": "not-a-number"},
                    "repos": [{"url": "git@h:a/x.git", "branch": "main"}],
                },
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(p)
            self.assertIn("telegram.chat_id", str(ctx.exception).lower())

    def test_telegram_inline_token_and_chat_id(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "telegram": {
                        "bot_token": "123:ABC",
                        "chat_id": 1380628864,
                    },
                    "repos": [{"url": "git@h:a/x.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.telegram.bot_token, "123:ABC")
            self.assertEqual(cfg.telegram.chat_id, "1380628864")
            tok, chat = telegram_credentials(cfg)
            self.assertEqual(tok, "123:ABC")
            self.assertEqual(chat, "1380628864")

    def test_telegram_inline_overrides_env(self) -> None:
        import os

        cfg = AppConfig(
            base_path=Path("/tmp"),
            poll_interval_seconds=60,
            state_file=Path("/tmp/state.json"),
            start_sh_timeout_seconds=60,
            start_sh_failure_retry_attempts=1,
            start_sh_failure_retry_interval_seconds=0,
            deploy_backoff_initial_seconds=10,
            deploy_backoff_max_seconds=300,
            ssh_identity_file=None,
            telegram=TelegramConfig(
                bot_token="from-config",
                chat_id="99",
                bot_token_env="TELEGRAM_BOT_TOKEN",
                chat_id_env="TELEGRAM_CHAT_ID",
            ),
            repos=(),
        )
        os.environ["TELEGRAM_BOT_TOKEN"] = "from-env"
        os.environ["TELEGRAM_CHAT_ID"] = "88"
        try:
            tok, chat = telegram_credentials(cfg)
        finally:
            del os.environ["TELEGRAM_BOT_TOKEN"]
            del os.environ["TELEGRAM_CHAT_ID"]
        self.assertEqual(tok, "from-config")
        self.assertEqual(chat, "99")

    def test_telegram_inline_allows_garbage_env_names_when_unused(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "telegram": {
                        "bot_token": "123:ABC",
                        "chat_id": 1,
                        "bot_token_env": "my token",
                        "chat_id_env": "not used",
                    },
                    "repos": [{"url": "git@h:a/x.git", "branch": "main"}],
                },
            )
            cfg = load_config(p)
            tok, chat = telegram_credentials(cfg)
            self.assertEqual(tok, "123:ABC")
            self.assertEqual(chat, "1")

    def test_telegram_bot_token_env_must_be_valid_env_name(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "telegram": {"bot_token_env": "my token"},
                    "repos": [{"url": "git@h:a/x.git", "branch": "main"}],
                },
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(p)
            self.assertIn("telegram.bot_token", str(ctx.exception).lower())


class TestBuildGitEnv(unittest.TestCase):
    def test_ssh_identity_sets_git_ssh_command(self) -> None:
        with TemporaryDirectory() as td:
            key = Path(td) / "k"
            key.write_text("fake", encoding="utf-8")
            cfg = AppConfig(
                base_path=Path("/tmp"),
                poll_interval_seconds=60,
                state_file=Path(td) / "state.json",
                start_sh_timeout_seconds=60,
                start_sh_failure_retry_attempts=1,
                start_sh_failure_retry_interval_seconds=0,
                deploy_backoff_initial_seconds=10,
                deploy_backoff_max_seconds=300,
                ssh_identity_file=key,
                telegram=TelegramConfig(bot_token=None, chat_id=None, bot_token_env="T", chat_id_env="C"),
                repos=(),
            )
            env = build_git_env(cfg, parent={})
            self.assertIn("GIT_SSH_COMMAND", env)
            self.assertIn(str(key), env["GIT_SSH_COMMAND"])

    def test_existing_git_ssh_command_wins(self) -> None:
        with TemporaryDirectory() as td:
            key = Path(td) / "k"
            key.write_text("fake", encoding="utf-8")
            cfg = AppConfig(
                base_path=Path("/tmp"),
                poll_interval_seconds=60,
                state_file=Path(td) / "state.json",
                start_sh_timeout_seconds=60,
                start_sh_failure_retry_attempts=1,
                start_sh_failure_retry_interval_seconds=0,
                deploy_backoff_initial_seconds=10,
                deploy_backoff_max_seconds=300,
                ssh_identity_file=key,
                telegram=TelegramConfig(bot_token=None, chat_id=None, bot_token_env="T", chat_id_env="C"),
                repos=(),
            )
            env = build_git_env(cfg, parent={"GIT_SSH_COMMAND": "ssh -i /already"})
            self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -i /already")


if __name__ == "__main__":
    unittest.main()
