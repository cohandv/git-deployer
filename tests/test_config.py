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
)


def _write_config(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


class TestLoadConfig(unittest.TestCase):
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
                ssh_identity_file=key,
                telegram=TelegramConfig(bot_token_env="T", chat_id_env="C"),
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
                ssh_identity_file=key,
                telegram=TelegramConfig(bot_token_env="T", chat_id_env="C"),
                repos=(),
            )
            env = build_git_env(cfg, parent={"GIT_SSH_COMMAND": "ssh -i /already"})
            self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -i /already")


if __name__ == "__main__":
    unittest.main()
