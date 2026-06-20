from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from git_deploy_watcher.config import ConfigError, load_config
from git_deploy_watcher.main import run_loop


def _write_config(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


class TestConfigReload(unittest.TestCase):
    def test_run_loop_uses_stale_config_when_reload_fails(self) -> None:
        valid = {
            "config_version": 2,
            "base_path": "/tmp/apps",
            "poll_interval_seconds": 1,
            "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
        }
        with TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            _write_config(p, valid)
            iterations = {"n": 0}

            def fake_sleep(seconds: float) -> None:
                iterations["n"] += 1
                if iterations["n"] == 1:
                    p.write_text("{ not json", encoding="utf-8")
                if iterations["n"] >= 2:
                    raise StopIteration

            with patch("git_deploy_watcher.main.time.sleep", fake_sleep):
                with patch("git_deploy_watcher.main.tick_repo") as tick:
                    with self.assertRaises(StopIteration):
                        run_loop(p)
                    self.assertEqual(tick.call_count, 2)

    def test_run_loop_picks_up_valid_config_after_initial_failure(self) -> None:
        valid = {
            "config_version": 2,
            "base_path": "/tmp/apps",
            "poll_interval_seconds": 1,
            "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
        }
        with TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text("{ bad", encoding="utf-8")
            iterations = {"n": 0}

            def fake_sleep(seconds: float) -> None:
                iterations["n"] += 1
                if iterations["n"] == 1:
                    _write_config(p, valid)
                if iterations["n"] >= 2:
                    raise StopIteration

            with patch("git_deploy_watcher.main.time.sleep", fake_sleep):
                with patch("git_deploy_watcher.main.tick_repo") as tick:
                    with self.assertRaises(StopIteration):
                        run_loop(p)
                    self.assertEqual(tick.call_count, 1)


if __name__ == "__main__":
    unittest.main()
