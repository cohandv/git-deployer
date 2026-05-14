from __future__ import annotations

import unittest

from git_deploy_watcher.main import DeployBackoffState


class TestDeployBackoffState(unittest.TestCase):
    def test_exponential_delay_capped(self) -> None:
        b = DeployBackoffState()
        d1 = b.on_deploy_failure("api", initial=10, cap=300)
        self.assertEqual(d1, 10.0)
        d2 = b.on_deploy_failure("api", initial=10, cap=300)
        self.assertEqual(d2, 20.0)
        d3 = b.on_deploy_failure("api", initial=10, cap=300)
        self.assertEqual(d3, 40.0)
        self.assertFalse(b.ready("api"))

    def test_cap_limits_delay(self) -> None:
        b = DeployBackoffState()
        d1 = b.on_deploy_failure("x", initial=100, cap=120)
        self.assertEqual(d1, 100.0)
        d2 = b.on_deploy_failure("x", initial=100, cap=120)
        self.assertEqual(d2, 120.0)

    def test_success_resets_streak(self) -> None:
        b = DeployBackoffState()
        b.on_deploy_failure("api", initial=5, cap=100)
        self.assertEqual(b.failure_streak("api"), 1)
        b.on_deploy_success("api")
        self.assertEqual(b.failure_streak("api"), 0)
        self.assertTrue(b.ready("api"))
        d = b.on_deploy_failure("api", initial=5, cap=100)
        self.assertEqual(d, 5.0)

    def test_per_repo_independent(self) -> None:
        b = DeployBackoffState()
        b.on_deploy_failure("a", initial=10, cap=300)
        self.assertTrue(b.ready("b"))


if __name__ == "__main__":
    unittest.main()
