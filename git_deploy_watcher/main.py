from __future__ import annotations

import argparse
import fcntl
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from git_deploy_watcher.config import AppConfig, build_git_env, load_config, telegram_credentials
from git_deploy_watcher.deploy import StartScriptError, run_start_sh
from git_deploy_watcher.git_ops import (
    GitError,
    clean_repo_fdx,
    clone_repo,
    fetch_merge_ff,
    is_dirty,
    rev_parse_head,
)
from git_deploy_watcher.notify import (
    TelegramRateLimiter,
    format_git_failure_alert,
    format_start_failure_alert,
    send_telegram_message,
    truncate_telegram_message,
)
from git_deploy_watcher.state import load_last_deployed, save_last_deployed

logger = logging.getLogger(__name__)

# Avoid huge journal lines if a script is very chatty.
_START_SH_LOG_MAX_CHARS = 65536


def _log_start_sh_streams(
    repo_name: str,
    stdout: str,
    stderr: str,
    *,
    failed: bool,
) -> None:
    """Log captured ``start.sh`` stdout/stderr (truncated if extremely long)."""
    log = logger.error if failed else logger.info
    has_out = bool((stdout or "").strip())
    has_err = bool((stderr or "").strip())
    if not has_out and not has_err:
        if failed:
            log("start.sh for %s failed with no stdout/stderr captured", repo_name)
        return
    for label, raw in ("stdout", stdout or ""), ("stderr", stderr or ""):
        if not (raw or "").strip():
            continue
        n = len(raw)
        text = raw
        if n > _START_SH_LOG_MAX_CHARS:
            text = raw[:_START_SH_LOG_MAX_CHARS] + f"\n... ({label} truncated, total {n} chars)\n"
        log("start.sh %s for %s (%s):\n%s", "failure" if failed else "success", repo_name, label, text.rstrip("\n"))


@dataclass
class DeployBackoffState:
    """Per-repo exponential backoff after ``start.sh`` failures (in-process only).

    Streak resets on successful deploy for that repo. A process restart clears all
    entries because this object is not persisted.
    """

    _streak: dict[str, int] = field(default_factory=dict)
    _next_try_monotonic: dict[str, float] = field(default_factory=dict)

    def ready(self, repo: str) -> bool:
        return time.monotonic() >= self._next_try_monotonic.get(repo, 0.0)

    def wait_seconds(self, repo: str) -> float:
        return max(0.0, self._next_try_monotonic.get(repo, 0.0) - time.monotonic())

    def on_deploy_success(self, repo: str) -> None:
        self._streak.pop(repo, None)
        self._next_try_monotonic.pop(repo, None)

    def on_deploy_failure(self, repo: str, *, initial: int, cap: int) -> float:
        """Bump failure streak and schedule the next attempt; returns delay used (seconds)."""
        s = self._streak.get(repo, 0) + 1
        self._streak[repo] = s
        raw = initial * (2 ** (s - 1))
        delay = float(min(cap, raw))
        self._next_try_monotonic[repo] = time.monotonic() + delay
        return delay

    def failure_streak(self, repo: str) -> int:
        return self._streak.get(repo, 0)


class RepoLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh: object | None = None

    def __enter__(self) -> RepoLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._fh is not None
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None


def _telegram_env(cfg: AppConfig) -> tuple[str | None, str | None]:
    return telegram_credentials(cfg)


def _notify_start_sh_failure(
    cfg: AppConfig,
    limiter: TelegramRateLimiter,
    *,
    repo_name: str,
    branch: str,
    head_after: str,
    err: StartScriptError,
) -> None:
    token, chat_id = _telegram_env(cfg)
    body = format_start_failure_alert(
        repo_name=repo_name,
        branch=branch,
        head_sha=head_after,
        exit_code=err.code,
        err_message=str(err),
        stderr=err.stderr,
        stdout=err.stdout,
    )

    if not token or not chat_id:
        logger.error("start.sh failure (Telegram not configured): %s", truncate_telegram_message(body, 2000))
        return
    rate_key = f"{repo_name}:start.sh"
    if not limiter.allow(rate_key):
        logger.warning("suppressed Telegram for %s (rate limit)", rate_key)
        return
    try:
        send_telegram_message(bot_token=token, chat_id=chat_id, text=body)
    except Exception:
        logger.exception("failed to send Telegram alert for %s", repo_name)


def _run_start_sh_with_retries(
    *,
    cfg: AppConfig,
    repo_name: str,
    repo_path: Path,
    git_env: dict[str, str],
) -> None:
    """Run ``start.sh`` up to ``cfg.start_sh_failure_retry_attempts`` times with backoff.

    On success after a prior failure in the same burst, logs at INFO. The outer poll
    loop still retries on the next tick if state was not updated (deploy not marked ok).
    """
    attempts = cfg.start_sh_failure_retry_attempts
    interval = cfg.start_sh_failure_retry_interval_seconds
    last: StartScriptError | None = None
    for attempt in range(1, attempts + 1):
        try:
            cp = run_start_sh(repo_path, git_env, cfg.start_sh_timeout_seconds)
            _log_start_sh_streams(repo_name, cp.stdout or "", cp.stderr or "", failed=False)
            if attempt > 1:
                logger.info("start.sh succeeded for %s on attempt %d/%d", repo_name, attempt, attempts)
            return
        except StartScriptError as e:
            last = e
            _log_start_sh_streams(repo_name, e.stdout, e.stderr, failed=True)
            if attempt >= attempts:
                break
            logger.warning(
                "start.sh failed for %s (attempt %d/%d), retrying in %ds: %s",
                repo_name,
                attempt,
                attempts,
                interval,
                e,
            )
            if interval > 0:
                time.sleep(interval)
    assert last is not None
    raise last


def _notify_git_failure(
    cfg: AppConfig,
    limiter: TelegramRateLimiter,
    *,
    repo_name: str,
    branch: str,
    phase: str,
    err: GitError,
    head_sha: str | None = None,
) -> None:
    token, chat_id = _telegram_env(cfg)
    body = format_git_failure_alert(
        repo_name=repo_name,
        branch=branch,
        phase=phase,
        exit_code=err.code,
        err_message=str(err),
        stderr=err.stderr,
        stdout=err.stdout,
        head_sha=head_sha,
    )

    if not token or not chat_id:
        logger.error("git failure (Telegram not configured): %s", truncate_telegram_message(body, 2000))
        return
    rate_key = f"{repo_name}:git"
    if not limiter.allow(rate_key):
        logger.warning("suppressed Telegram for %s (rate limit)", rate_key)
        return
    try:
        send_telegram_message(bot_token=token, chat_id=chat_id, text=body)
    except Exception:
        logger.exception("failed to send Telegram git alert for %s", repo_name)


def tick_repo(
    cfg: AppConfig,
    git_env: dict[str, str],
    deployed: dict[str, str],
    limiter: TelegramRateLimiter,
    backoff: DeployBackoffState,
) -> None:
    for repo in cfg.repos:
        lock_path = cfg.state_file.parent / "locks" / f"{repo.name}.lock"
        with RepoLock(lock_path):
            repo_path = cfg.base_path / repo.name
            last_ok = deployed.get(repo.name)
            try:
                if not repo_path.exists():
                    logger.info("cloning %s into %s", repo.name, repo_path)
                    try:
                        clone_repo(repo_path, repo.url, repo.branch, git_env)
                        head = rev_parse_head(repo_path, git_env)
                    except GitError as e:
                        logger.error("git clone/setup failed for %s: %s", repo.name, e)
                        head_hint: str | None = None
                        if repo_path.exists():
                            try:
                                head_hint = rev_parse_head(repo_path, git_env)
                            except GitError:
                                pass
                        _notify_git_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            phase="clone",
                            err=e,
                            head_sha=head_hint,
                        )
                        continue
                    logger.info("cloned %s at %s", repo.name, head)
                    if not backoff.ready(repo.name):
                        rem = int(backoff.wait_seconds(repo.name) + 0.999)
                        logger.info(
                            "skipping start.sh for %s after clone (backoff, ~%ds left)",
                            repo.name,
                            rem,
                        )
                        continue
                    try:
                        _run_start_sh_with_retries(
                            cfg=cfg,
                            repo_name=repo.name,
                            repo_path=repo_path,
                            git_env=git_env,
                        )
                    except StartScriptError as e:
                        logger.error("start.sh failed after clone for %s: %s", repo.name, e)
                        delay = backoff.on_deploy_failure(
                            repo.name,
                            initial=cfg.deploy_backoff_initial_seconds,
                            cap=cfg.deploy_backoff_max_seconds,
                        )
                        logger.warning(
                            "deploy backoff for %s after clone failure: next try in %.0fs (streak=%d)",
                            repo.name,
                            delay,
                            backoff.failure_streak(repo.name),
                        )
                        _notify_start_sh_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            head_after=head,
                            err=e,
                        )
                        continue
                    backoff.on_deploy_success(repo.name)
                    deployed[repo.name] = head
                    save_last_deployed(cfg.state_file, deployed)
                    continue

                try:
                    dirty = is_dirty(repo_path, git_env)
                except GitError as e:
                    logger.error("git status failed for %s: %s", repo.name, e)
                    head_hint: str | None = None
                    try:
                        head_hint = rev_parse_head(repo_path, git_env)
                    except GitError:
                        pass
                    _notify_git_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        phase="status",
                        err=e,
                        head_sha=head_hint,
                    )
                    continue

                if dirty:
                    logger.info(
                        "repo %s: working tree dirty; running git clean -fdx (normal cleanup, not a failure)",
                        repo.name,
                    )
                    try:
                        clean_repo_fdx(repo_path, git_env)
                    except GitError as e:
                        logger.error("git clean -fdx failed for %s: %s", repo.name, e)
                        head_hint: str | None = None
                        try:
                            head_hint = rev_parse_head(repo_path, git_env)
                        except GitError:
                            pass
                        _notify_git_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            phase="clean",
                            err=e,
                            head_sha=head_hint,
                        )
                        continue
                    try:
                        still_dirty = is_dirty(repo_path, git_env)
                    except GitError as e:
                        logger.error("git status failed for %s after clean: %s", repo.name, e)
                        _notify_git_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            phase="status",
                            err=e,
                        )
                        continue
                    if still_dirty:
                        logger.error(
                            "repo %s still dirty after git clean -fdx (tracked changes or merge state); skipping pull",
                            repo.name,
                        )
                        head_hint: str | None = None
                        try:
                            head_hint = rev_parse_head(repo_path, git_env)
                        except GitError:
                            pass
                        _notify_git_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            phase="post-clean",
                            err=GitError(
                                "working tree still dirty after git clean -fdx (only removes untracked/ignored files)"
                            ),
                            head_sha=head_hint,
                        )
                        continue

                try:
                    head_before = rev_parse_head(repo_path, git_env)
                except GitError as e:
                    logger.error("git rev-parse failed for %s: %s", repo.name, e)
                    _notify_git_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        phase="rev-parse(before)",
                        err=e,
                    )
                    continue

                try:
                    fetch_merge_ff(repo_path, repo.branch, git_env)
                except GitError as e:
                    logger.error("git update failed for %s: %s", repo.name, e)
                    _notify_git_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        phase="fetch/checkout/merge",
                        err=e,
                        head_sha=head_before,
                    )
                    continue

                try:
                    head_after = rev_parse_head(repo_path, git_env)
                except GitError as e:
                    logger.error("git rev-parse failed for %s: %s", repo.name, e)
                    _notify_git_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        phase="rev-parse(after)",
                        err=e,
                        head_sha=head_before,
                    )
                    continue

                if head_after != head_before:
                    logger.info("repo %s updated %s -> %s", repo.name, head_before, head_after)

                if last_ok is not None and head_after == last_ok:
                    continue

                # Avoid running start.sh every poll when no commit advanced: state_file may never
                # get updated if start.sh restarts this service (process dies before save). Still
                # run when we are retrying after start.sh failure (backoff streak > 0 or waiting).
                if (
                    last_ok is None
                    and head_after == head_before
                    and backoff.failure_streak(repo.name) == 0
                    and backoff.ready(repo.name)
                ):
                    logger.info(
                        "repo %s: no new commits on the remote this fetch and no successful deploy "
                        "recorded in %s — skipping start.sh until the branch advances or you add "
                        "\"%s\": \"<HEAD-sha>\" to that file (prevents restart loops when start.sh "
                        "restarts the watcher before state is saved)",
                        repo.name,
                        cfg.state_file,
                        repo.name,
                    )
                    continue

                if not backoff.ready(repo.name):
                    rem = int(backoff.wait_seconds(repo.name) + 0.999)
                    logger.info(
                        "skipping start.sh for %s (backoff, ~%ds left)",
                        repo.name,
                        rem,
                    )
                    continue

                try:
                    _run_start_sh_with_retries(
                        cfg=cfg,
                        repo_name=repo.name,
                        repo_path=repo_path,
                        git_env=git_env,
                    )
                except StartScriptError as e:
                    logger.error("start.sh failed for %s: %s", repo.name, e)
                    delay = backoff.on_deploy_failure(
                        repo.name,
                        initial=cfg.deploy_backoff_initial_seconds,
                        cap=cfg.deploy_backoff_max_seconds,
                    )
                    logger.warning(
                        "deploy backoff for %s: next try in %.0fs (streak=%d)",
                        repo.name,
                        delay,
                        backoff.failure_streak(repo.name),
                    )
                    _notify_start_sh_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        head_after=head_after,
                        err=e,
                    )
                    continue

                backoff.on_deploy_success(repo.name)
                deployed[repo.name] = head_after
                save_last_deployed(cfg.state_file, deployed)
                logger.info("deployed %s at %s", repo.name, head_after)
            except OSError as e:
                logger.exception("OS error for %s: %s", repo.name, e)


def run_loop(cfg: AppConfig) -> None:
    git_env = build_git_env(cfg)
    limiter = TelegramRateLimiter()
    backoff = DeployBackoffState()
    while True:
        deployed = load_last_deployed(cfg.state_file)
        try:
            tick_repo(cfg, git_env, deployed, limiter, backoff)
        except Exception:
            logger.exception("tick failed")
        time.sleep(cfg.poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll git repos and run start.sh on changes.")
    parser.add_argument("--config", required=True, type=Path, help="Path to config.json")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    try:
        cfg = load_config(args.config)
    except Exception as e:
        logger.error("invalid config: %s", e)
        return 2

    cfg.base_path.mkdir(parents=True, exist_ok=True)
    run_loop(cfg)
    return 0
