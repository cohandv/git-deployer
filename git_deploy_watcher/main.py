from __future__ import annotations

import argparse
import fcntl
import logging
import sys
import time
from pathlib import Path
from types import TracebackType

from git_deploy_watcher.config import AppConfig, build_git_env, load_config, telegram_credentials
from git_deploy_watcher.deploy import StartScriptError, run_start_sh
from git_deploy_watcher.git_ops import GitError, clone_repo, fetch_merge_ff, is_dirty, rev_parse_head
from git_deploy_watcher.notify import (
    TelegramRateLimiter,
    format_git_failure_alert,
    format_start_failure_alert,
    send_telegram_message,
    truncate_telegram_message,
)
from git_deploy_watcher.state import load_last_deployed, save_last_deployed

logger = logging.getLogger(__name__)


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


def tick_repo(cfg: AppConfig, git_env: dict[str, str], deployed: dict[str, str], limiter: TelegramRateLimiter) -> None:
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
                    try:
                        run_start_sh(repo_path, git_env, cfg.start_sh_timeout_seconds)
                    except StartScriptError as e:
                        logger.error("start.sh failed after clone for %s: %s", repo.name, e)
                        _notify_start_sh_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            head_after=head,
                            err=e,
                        )
                        continue
                    deployed[repo.name] = head
                    save_last_deployed(cfg.state_file, deployed)
                    continue

                try:
                    if is_dirty(repo_path, git_env):
                        logger.warning(
                            "repo %s has a dirty working tree; not treating as new revision",
                            repo.name,
                        )
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

                try:
                    run_start_sh(repo_path, git_env, cfg.start_sh_timeout_seconds)
                except StartScriptError as e:
                    logger.error("start.sh failed for %s: %s", repo.name, e)
                    _notify_start_sh_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        head_after=head_after,
                        err=e,
                    )
                    continue

                deployed[repo.name] = head_after
                save_last_deployed(cfg.state_file, deployed)
                logger.info("deployed %s at %s", repo.name, head_after)
            except OSError as e:
                logger.exception("OS error for %s: %s", repo.name, e)


def run_loop(cfg: AppConfig) -> None:
    git_env = build_git_env(cfg)
    limiter = TelegramRateLimiter()
    while True:
        deployed = load_last_deployed(cfg.state_file)
        try:
            tick_repo(cfg, git_env, deployed, limiter)
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
