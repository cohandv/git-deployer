from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from git_deploy_watcher.deploy_trigger import (
    drain_triggers,
    peek_pending,
    request_deploy,
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
            names = drain_triggers(state)
            self.assertEqual(names, {"api"})
            self.assertFalse(peek_pending(state))
            self.assertFalse(any(triggers_dir(state).glob("*.json")))

    def test_drain_merges_file_and_memory(self) -> None:
        with TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text("{}", encoding="utf-8")
            request_deploy(state, "a")
            request_deploy(state, "b")
            names = drain_triggers(state)
            self.assertEqual(names, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
