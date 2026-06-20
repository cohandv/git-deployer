from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from git_deploy_watcher.admin.server import start_admin_server


def _write_config(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, json.loads(raw) if raw else {}


class TestAdminAPI(unittest.TestCase):
    def setUp(self) -> None:
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.config_path = Path(self._td.name) / "config.json"
        self.state_file = Path(self._td.name) / "state.json"
        _write_config(
            self.config_path,
            {
                "config_version": 2,
                "base_path": "/tmp/apps",
                "state_file": str(self.state_file),
                "repos": [{"name": "api", "url": "git@github.com:org/api.git", "branch": "main"}],
            },
        )
        self.server = start_admin_server(self.config_path, host="127.0.0.1", port=0)
        self.host, self.port = self.server.server_address
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_get_config(self) -> None:
        data = _get_json(f"{self.base}/api/config")
        self.assertTrue(data["validation_ok"])
        self.assertEqual(data["config"]["config_version"], 2)

    def test_post_invalid_url_returns_400(self) -> None:
        body = {
            "config_version": 2,
            "base_path": "/tmp/apps",
            "repos": [{"url": "https://github.com/org/foo.git", "branch": "main"}],
        }
        status, data = _post_json(f"{self.base}/api/config", body)
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertTrue(any("repos" in e.get("path", "") for e in data["errors"]))

    def test_post_valid_updates_file(self) -> None:
        body = {
            "config_version": 2,
            "base_path": "/var/deploy/apps",
            "poll_interval_seconds": 45,
            "repos": [{"name": "api", "url": "git@github.com:org/api.git", "branch": "main"}],
        }
        status, data = _post_json(f"{self.base}/api/config", body)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["poll_interval_seconds"], 45)
        history = _get_json(f"{self.base}/api/history")
        self.assertGreaterEqual(len(history["history"]), 1)

    def test_post_repo_deploy(self) -> None:
        status, data = _post_json(f"{self.base}/api/repos/api/deploy", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["deploy_queued"], ["api"])

    def test_post_repo_deploy_unknown(self) -> None:
        status, data = _post_json(f"{self.base}/api/repos/missing/deploy", {})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_post_config_with_deploy_query(self) -> None:
        body = {
            "config_version": 2,
            "base_path": "/var/deploy/apps",
            "state_file": str(self.state_file),
            "repos": [{"name": "api", "url": "git@github.com:org/api.git", "branch": "main"}],
        }
        url = f"{self.base}/api/config?deploy=api"
        status, data = _post_json(url, body)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data.get("deploy_queued"), ["api"])


if __name__ == "__main__":
    unittest.main()
