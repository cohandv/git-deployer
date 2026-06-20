from __future__ import annotations

import json
import shlex
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from git_deploy_watcher.config import (
    AppConfig,
    ConfigError,
    RepoConfig,
    TelegramConfig,
    build_git_env,
    build_start_sh_env,
    load_config,
    load_config_dict,
    telegram_credentials,
)
from git_deploy_watcher.config_migrate import CURRENT_CONFIG_VERSION, migrate


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

    def test_repo_ssh_identity_builds_git_ssh_command_repo_key_first(self) -> None:
        with TemporaryDirectory() as td:
            k_default = Path(td) / "id_default"
            k_repo = Path(td) / "id_repo"
            k_default.write_text("k1", encoding="utf-8")
            k_repo.write_text("k2", encoding="utf-8")
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "ssh_identity_file": str(k_default),
                    "repos": [
                        {
                            "name": "r",
                            "url": "git@github.com:org/r.git",
                            "branch": "main",
                            "ssh_identity_file": str(k_repo),
                        }
                    ],
                },
            )
            cfg = load_config(p)
            self.assertEqual(cfg.repos[0].ssh_identity_file, k_repo)
            env = build_git_env(cfg, repo=cfg.repos[0], parent={})
            cmd = env["GIT_SSH_COMMAND"]
            q_repo = shlex.quote(str(k_repo))
            q_def = shlex.quote(str(k_default))
            self.assertIn(q_repo, cmd)
            self.assertIn(q_def, cmd)
            self.assertLess(cmd.index(q_repo), cmd.index(q_def))

    def test_repo_ssh_identity_file_rejects_empty_string(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "base_path": "/tmp/apps",
                    "repos": [
                        {
                            "name": "r",
                            "url": "git@github.com:org/r.git",
                            "branch": "main",
                            "ssh_identity_file": "  ",
                        }
                    ],
                },
            )
            with self.assertRaises(ConfigError) as ctx:
                load_config(p)
            self.assertIn("ssh_identity_file", str(ctx.exception))

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
            self.assertIsNone(cfg.repos[0].ssh_identity_file)

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
            config_version=2,
            poll_interval_seconds=60,
            state_file=Path("/tmp/state.json"),
            start_sh_timeout_seconds=60,
            start_sh_failure_retry_attempts=1,
            start_sh_failure_retry_interval_seconds=0,
            deploy_backoff_initial_seconds=10,
            deploy_backoff_max_seconds=300,
            ssh_identity_file=None,
            start_sh_env={},
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

    def test_v1_migrates_to_v2(self) -> None:
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
            self.assertEqual(cfg.config_version, CURRENT_CONFIG_VERSION)
            self.assertEqual(dict(cfg.start_sh_env), {})
            self.assertEqual(dict(cfg.repos[0].env), {})

    def test_v2_env_fields(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "config_version": 2,
                    "base_path": "/tmp/apps",
                    "start_sh_env": {"LOG_LEVEL": "debug"},
                    "repos": [
                        {
                            "url": "git@github.com:org/foo.git",
                            "branch": "main",
                            "env": {"PORT": "8080"},
                        }
                    ],
                },
            )
            cfg = load_config(p)
            self.assertEqual(dict(cfg.start_sh_env), {"LOG_LEVEL": "debug"})
            self.assertEqual(dict(cfg.repos[0].env), {"PORT": "8080"})

    def test_rejects_unknown_config_version(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            _write_config(
                p,
                {
                    "config_version": 99,
                    "base_path": "/tmp/apps",
                    "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
                },
            )
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_rejects_invalid_env_key(self) -> None:
        data, _ = migrate(
            {
                "base_path": "/tmp/apps",
                "start_sh_env": {"bad-key": "x"},
                "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
            }
        )
        with self.assertRaises(ConfigError):
            load_config_dict(data)


class TestBuildStartShEnv(unittest.TestCase):
    def test_merge_order_and_watcher_vars_win(self) -> None:
        import os

        with TemporaryDirectory() as td:
            base = Path(td) / "apps"
            base.mkdir()
            repo_dir = base / "myapp"
            repo_dir.mkdir()
            cfg = AppConfig(
                base_path=base,
                config_version=2,
                poll_interval_seconds=60,
                state_file=Path(td) / "state.json",
                start_sh_timeout_seconds=60,
                start_sh_failure_retry_attempts=1,
                start_sh_failure_retry_interval_seconds=0,
                deploy_backoff_initial_seconds=10,
                deploy_backoff_max_seconds=300,
                ssh_identity_file=None,
                start_sh_env={"PORT": "1", "GIT_DEPLOY_REPO_ROOT": "bad"},
                telegram=TelegramConfig(bot_token=None, chat_id=None, bot_token_env="T", chat_id_env="C"),
                repos=(
                    RepoConfig(
                        name="myapp",
                        url="git@github.com:org/myapp.git",
                        branch="main",
                        ssh_identity_file=None,
                        env={"PORT": "8080"},
                    ),
                ),
            )
            old_port = os.environ.get("PORT")
            os.environ["PORT"] = "9999"
            try:
                env = build_start_sh_env(cfg, cfg.repos[0])
            finally:
                if old_port is None:
                    os.environ.pop("PORT", None)
                else:
                    os.environ["PORT"] = old_port
            self.assertEqual(env["PORT"], "8080")
            self.assertEqual(env["GIT_DEPLOY_REPO_ROOT"], str(repo_dir.resolve()))
            self.assertEqual(env["PWD"], str(repo_dir.resolve()))


class TestBuildGitEnv(unittest.TestCase):
    def test_ssh_identity_sets_git_ssh_command(self) -> None:
        with TemporaryDirectory() as td:
            key = Path(td) / "k"
            key.write_text("fake", encoding="utf-8")
            cfg = AppConfig(
                base_path=Path("/tmp"),
                config_version=2,
                poll_interval_seconds=60,
                state_file=Path(td) / "state.json",
                start_sh_timeout_seconds=60,
                start_sh_failure_retry_attempts=1,
                start_sh_failure_retry_interval_seconds=0,
                deploy_backoff_initial_seconds=10,
                deploy_backoff_max_seconds=300,
                ssh_identity_file=key,
                start_sh_env={},
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
                config_version=2,
                poll_interval_seconds=60,
                state_file=Path(td) / "state.json",
                start_sh_timeout_seconds=60,
                start_sh_failure_retry_attempts=1,
                start_sh_failure_retry_interval_seconds=0,
                deploy_backoff_initial_seconds=10,
                deploy_backoff_max_seconds=300,
                ssh_identity_file=key,
                start_sh_env={},
                telegram=TelegramConfig(bot_token=None, chat_id=None, bot_token_env="T", chat_id_env="C"),
                repos=(),
            )
            env = build_git_env(cfg, parent={"GIT_SSH_COMMAND": "ssh -i /already"})
            self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -i /already")

    def test_repo_and_global_same_path_single_minus_i(self) -> None:
        with TemporaryDirectory() as td:
            key = Path(td) / "shared"
            key.write_text("x", encoding="utf-8")
            cfg = AppConfig(
                base_path=Path("/tmp"),
                config_version=2,
                poll_interval_seconds=60,
                state_file=Path(td) / "state.json",
                start_sh_timeout_seconds=60,
                start_sh_failure_retry_attempts=1,
                start_sh_failure_retry_interval_seconds=0,
                deploy_backoff_initial_seconds=10,
                deploy_backoff_max_seconds=300,
                ssh_identity_file=key,
                start_sh_env={},
                telegram=TelegramConfig(bot_token=None, chat_id=None, bot_token_env="T", chat_id_env="C"),
                repos=(),
            )
            repo = RepoConfig(
                name="a",
                url="git@github.com:org/a.git",
                branch="main",
                ssh_identity_file=key,
                env={},
            )
            env = build_git_env(cfg, repo=repo, parent={})
            self.assertEqual(env["GIT_SSH_COMMAND"].count("-i "), 1)


if __name__ == "__main__":
    unittest.main()
