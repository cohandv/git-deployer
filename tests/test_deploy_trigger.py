from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from git_deploy_watcher.deploy_trigger import (
    drain_triggers,
    peek_pending,
    request_deploy,
    request_sync,
    triggers_dir,
)


class TestDeployTrigger(unittest.TestCase):
    def test_request_and_drain(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text("{}", encoding="utf-8")
            self.assertFalse(peek_pending(state))
            request_deploy(state, "api")
            self.assertTrue(peek_pending(state))
            batch = drain_triggers(state)
            self.assertEqual(batch.deploy, frozenset({"api"}))
            self.assertEqual(batch.sync, frozenset())
            self.assertFalse(peek_pending(state))
            self.assertFalse(any(triggers_dir(state).glob("*.json")))

    def test_drain_merges_file_and_memory(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text("{}", encoding="utf-8")
            request_deploy(state, "a")
            request_sync(state, "b")
            batch = drain_triggers(state)
            self.assertEqual(batch.deploy, frozenset({"a"}))
            self.assertEqual(batch.sync, frozenset({"b"}))

    def test_deploy_overrides_sync_for_same_repo(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text("{}", encoding="utf-8")
            request_sync(state, "api")
            request_deploy(state, "api")
            batch = drain_triggers(state)
            self.assertEqual(batch.deploy, frozenset({"api"}))
            self.assertEqual(batch.sync, frozenset())


if __name__ == "__main__":
    unittest.main()
