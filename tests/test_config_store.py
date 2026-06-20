from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from git_deploy_watcher.config_migrate import canonical_json, migrate
from git_deploy_watcher.config_store import diff_configs, list_history, load_history, save_config


def _base_config() -> dict:
    return {
        "config_version": 2,
        "base_path": "/tmp/apps",
        "repos": [{"url": "git@github.com:org/foo.git", "branch": "main"}],
    }


class TestConfigStore(unittest.TestCase):
    def test_save_creates_history_and_atomic_write(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            first = _base_config()
            save_config(p, first)
            self.assertTrue(p.is_file())
            loaded = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(loaded["config_version"], 2)

            second = dict(first)
            second["poll_interval_seconds"] = 120
            save_config(p, second)
            history = list_history(p)
            self.assertEqual(len(history), 1)
            snap = load_history(p, history[0]["id"])
            self.assertNotIn("poll_interval_seconds", snap) or snap.get("poll_interval_seconds") != 120

            current = load_history(p, "current")
            self.assertEqual(current.get("poll_interval_seconds"), 120)

    def test_diff_configs(self) -> None:
        a = _base_config()
        b = dict(a)
        b["poll_interval_seconds"] = 90
        text = diff_configs(a, b, from_label="a", to_label="b")
        self.assertIn("poll_interval_seconds", text)

    def test_canonical_json_stable(self) -> None:
        data = {"b": 1, "a": 2}
        self.assertIn('"a"', canonical_json(data))


if __name__ == "__main__":
    unittest.main()
