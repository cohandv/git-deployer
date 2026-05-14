from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    def __init__(self, message: str, *, stdout: str = "", stderr: str = "", code: int | None = None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.code = code


def _run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    logger.debug("git %s (cwd=%s)", " ".join(args), cwd)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_ok(cp: subprocess.CompletedProcess[str], what: str) -> None:
    if cp.returncode != 0:
        raise GitError(
            f"{what} failed (exit {cp.returncode})",
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
            code=cp.returncode,
        )


def rev_parse_head(repo: Path, env: dict[str, str]) -> str:
    cp = _run_git(["rev-parse", "HEAD"], cwd=repo, env=env)
    _require_ok(cp, "git rev-parse HEAD")
    sha = (cp.stdout or "").strip()
    if len(sha) < 7:
        raise GitError("unexpected short SHA from rev-parse", stdout=cp.stdout or "")
    return sha


def is_dirty(repo: Path, env: dict[str, str]) -> bool:
    cp = _run_git(["status", "--porcelain"], cwd=repo, env=env)
    _require_ok(cp, "git status --porcelain")
    return bool((cp.stdout or "").strip())


def clean_repo_fdx(repo: Path, env: dict[str, str]) -> None:
    """Remove untracked and ignored files so a deploy checkout can update."""
    cp = _run_git(["clean", "-fdx"], cwd=repo, env=env)
    _require_ok(cp, "git clean -fdx")


def clone_repo(repo: Path, url: str, branch: str, env: dict[str, str]) -> None:
    repo.parent.mkdir(parents=True, exist_ok=True)
    cp = _run_git(
        [
            "clone",
            "--branch",
            branch,
            "--single-branch",
            url,
            str(repo),
        ],
        cwd=None,
        env=env,
    )
    _require_ok(cp, "git clone")


def fetch_merge_ff(repo: Path, branch: str, env: dict[str, str]) -> None:
    cp = _run_git(["fetch", "origin"], cwd=repo, env=env)
    _require_ok(cp, "git fetch origin")
    cp = _run_git(["checkout", branch], cwd=repo, env=env)
    _require_ok(cp, f"git checkout {branch}")
    cp = _run_git(["merge", "--ff-only", f"origin/{branch}"], cwd=repo, env=env)
    _require_ok(cp, f"git merge --ff-only origin/{branch}")
