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
from git_deploy_watcher.notify import TelegramRateLimiter, send_telegram_message, truncate_telegram_message
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


def _tail(text: str, max_lines: int = 40, max_chars: int = 3500) -> str:
    lines = (text or "").splitlines()
    if len(lines) > max_lines:
        lines = ["…"] + lines[-max_lines:]
    s = "\n".join(lines)
    if len(s) > max_chars:
        s = "…" + s[-max_chars:]
    return s


def _telegram_env(cfg: AppConfig) -> tuple[str | None, str | None]:
    return telegram_credentials(cfg)


def _notify_start_sh_failure(
    cfg: AppConfig,
    limiter: TelegramRateLimiter,
    *,
    repo_name: str,
    branch: str,
    head_before: str | None,
    head_after: str,
    err: StartScriptError,
) -> None:
    token, chat_id = _telegram_env(cfg)
    parts = [
        "[start.sh failed]",
        f"repo={repo_name}",
        f"branch={branch}",
        f"head={head_after}",
    ]
    if head_before is not None:
        parts.append(f"last_deployed={head_before}")
    parts.append(f"start_sh_exit={err.code}")
    parts.append("--- start.sh stdout ---")
    parts.append(_tail(err.stdout))
    parts.append("--- start.sh stderr ---")
    parts.append(_tail(err.stderr))
    body = "\n".join(parts)

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
    url: str,
    phase: str,
    err: GitError,
) -> None:
    token, chat_id = _telegram_env(cfg)
    parts = [
        "[git failed]",
        f"repo={repo_name}",
        f"branch={branch}",
        f"url={url}",
        f"phase={phase}",
        f"exit={err.code}",
        str(err),
        "--- git stderr ---",
        _tail(err.stderr),
        "--- git stdout ---",
        _tail(err.stdout),
    ]
    body = "\n".join(parts)

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
                        _notify_git_failure(
                            cfg,
                            limiter,
                            repo_name=repo.name,
                            branch=repo.branch,
                            url=repo.url,
                            phase="clone",
                            err=e,
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
                            head_before=None,
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
                    _notify_git_failure(
                        cfg,
                        limiter,
                        repo_name=repo.name,
                        branch=repo.branch,
                        url=repo.url,
                        phase="status",
                        err=e,
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
                        url=repo.url,
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
                        url=repo.url,
                        phase="fetch/checkout/merge",
                        err=e,
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
                        url=repo.url,
                        phase="rev-parse(after)",
                        err=e,
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
                        head_before=last_ok,
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
