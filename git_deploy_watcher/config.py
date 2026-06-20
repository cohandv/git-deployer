from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from git_deploy_watcher.config_migrate import CURRENT_CONFIG_VERSION, ConfigError, migrate, parse_raw_text


@dataclass(frozen=True)
class ConfigFieldError:
    path: str
    message: str


class ConfigValidationError(ConfigError):
    def __init__(self, errors: list[ConfigFieldError]):
        self.errors = errors
        if len(errors) == 1:
            msg = errors[0].message if not errors[0].path else f"{errors[0].path}: {errors[0].message}"
        else:
            msg = "; ".join(f"{e.path}: {e.message}" if e.path else e.message for e in errors)
        super().__init__(msg)


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram credentials: optional literals plus env fallbacks per field."""

    bot_token: str | None
    chat_id: str | None
    bot_token_env: str
    chat_id_env: str


@dataclass(frozen=True)
class RepoConfig:
    name: str
    url: str
    branch: str
    ssh_identity_file: Path | None
    env: Mapping[str, str]


@dataclass(frozen=True)
class AppConfig:
    config_version: int
    base_path: Path
    poll_interval_seconds: int
    state_file: Path
    start_sh_timeout_seconds: int
    start_sh_failure_retry_attempts: int
    start_sh_failure_retry_interval_seconds: int
    deploy_backoff_initial_seconds: int
    deploy_backoff_max_seconds: int
    ssh_identity_file: Path | None
    start_sh_env: Mapping[str, str]
    telegram: TelegramConfig
    repos: tuple[RepoConfig, ...]


_SCP_STYLE = re.compile(r"^git@[^:]+:.+$")
_SSH_URL = re.compile(r"^ssh://", re.IGNORECASE)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WATCHER_ENV_KEYS = frozenset({"PWD", "GIT_DEPLOY_REPO_ROOT"})


def _is_ssh_git_url(url: str) -> bool:
    u = url.strip()
    if _SSH_URL.match(u):
        return True
    if _SCP_STYLE.match(u):
        return True
    return False


def _derive_name_from_url(url: str) -> str:
    u = url.strip().rstrip("/")
    if u.lower().startswith("ssh://"):
        parsed = urlparse(u)
        path = parsed.path or ""
        path = path.strip("/")
        base = path.rsplit("/", 1)[-1] if path else ""
    else:
        _, _, rest = u.partition(":")
        path = rest.strip("/")
        base = path.rsplit("/", 1)[-1] if path else ""
    if base.endswith(".git"):
        base = base[: -len(".git")]
    if not base:
        raise ConfigValidationError([ConfigFieldError("", f"cannot derive repo name from url: {url!r}")])
    return base


def _looks_like_telegram_bot_token(value: str) -> bool:
    s = value.strip()
    m = re.fullmatch(r"(\d+):([A-Za-z0-9_-]+)", s)
    if not m:
        return False
    return len(m.group(2)) >= 15


def _migrate_telegram_misplaced_keys(
    bot_token: str | None,
    chat_id: str | None,
    bte: str,
    cie: str,
) -> tuple[str | None, str | None, str, str]:
    if bot_token is None and not _ENV_NAME.match(bte) and _looks_like_telegram_bot_token(bte):
        bot_token = bte.strip()
        bte = "TELEGRAM_BOT_TOKEN"
    if chat_id is None and not _ENV_NAME.match(cie) and re.fullmatch(r"-?\d{1,20}", cie):
        chat_id = cie.strip()
        cie = "TELEGRAM_CHAT_ID"
    return bot_token, chat_id, bte, cie


def _parse_env_map(raw: Any, path: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigValidationError([ConfigFieldError(path, "must be an object")])
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _ENV_NAME.match(key):
            raise ConfigValidationError(
                [ConfigFieldError(f"{path}.{key}" if key else path, "key must be a valid environment variable name")]
            )
        if key in _WATCHER_ENV_KEYS:
            raise ConfigValidationError(
                [ConfigFieldError(f"{path}.{key}", "cannot override watcher-injected variables")]
            )
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (int, float)):
            out[key] = str(value)
        elif isinstance(value, str):
            out[key] = value
        else:
            raise ConfigValidationError([ConfigFieldError(f"{path}.{key}", "value must be a string or number")])
    return out


def telegram_credentials(cfg: AppConfig) -> tuple[str | None, str | None]:
    t = cfg.telegram.bot_token
    if t is None or not str(t).strip():
        t = os.environ.get(cfg.telegram.bot_token_env)
    else:
        t = str(t).strip()
    c = cfg.telegram.chat_id
    if c is None or not str(c).strip():
        c = os.environ.get(cfg.telegram.chat_id_env)
    else:
        c = str(c).strip()
    return (t or None, c or None)


def validate_config(data: dict[str, Any]) -> AppConfig:
    errors: list[ConfigFieldError] = []

    def err(path: str, message: str) -> None:
        errors.append(ConfigFieldError(path, message))

    version = data.get("config_version", CURRENT_CONFIG_VERSION)
    if not isinstance(version, int):
        err("config_version", "must be an integer")
    elif version != CURRENT_CONFIG_VERSION:
        err("config_version", f"must be {CURRENT_CONFIG_VERSION} after migration")

    base_path = data.get("base_path")
    if not isinstance(base_path, str) or not base_path.strip():
        err("base_path", "must be a non-empty string")
    base = Path(base_path).expanduser() if isinstance(base_path, str) and base_path.strip() else Path(".")

    poll = data.get("poll_interval_seconds", 60)
    if not isinstance(poll, int) or poll < 1:
        err("poll_interval_seconds", "must be an integer >= 1")

    state_file_raw = data.get("state_file", "/var/lib/git-deploy-watcher/state.json")
    if not isinstance(state_file_raw, str) or not state_file_raw.strip():
        err("state_file", "must be a non-empty string")
    state_file = (
        Path(state_file_raw).expanduser()
        if isinstance(state_file_raw, str) and state_file_raw.strip()
        else Path("/tmp/state.json")
    )

    timeout = data.get("start_sh_timeout_seconds", 300)
    if not isinstance(timeout, int) or timeout < 1:
        err("start_sh_timeout_seconds", "must be an integer >= 1")

    retry_attempts = data.get("start_sh_failure_retry_attempts", 5)
    if not isinstance(retry_attempts, int) or retry_attempts < 1:
        err("start_sh_failure_retry_attempts", "must be an integer >= 1")

    retry_interval = data.get("start_sh_failure_retry_interval_seconds", 10)
    if not isinstance(retry_interval, int) or retry_interval < 0:
        err("start_sh_failure_retry_interval_seconds", "must be an integer >= 0")

    backoff_initial = data.get("deploy_backoff_initial_seconds", 10)
    if not isinstance(backoff_initial, int) or backoff_initial < 1:
        err("deploy_backoff_initial_seconds", "must be an integer >= 1")

    backoff_max = data.get("deploy_backoff_max_seconds", 300)
    if not isinstance(backoff_max, int) or backoff_max < 1:
        err("deploy_backoff_max_seconds", "must be an integer >= 1")
    elif isinstance(backoff_initial, int) and isinstance(backoff_max, int) and backoff_max < backoff_initial:
        err("deploy_backoff_max_seconds", "must be >= deploy_backoff_initial_seconds")

    ssh_identity: Path | None = None
    if "ssh_identity_file" in data and data["ssh_identity_file"] is not None:
        sif = data["ssh_identity_file"]
        if not isinstance(sif, str) or not sif.strip():
            err("ssh_identity_file", "must be a non-empty string when set")
        else:
            ssh_identity = Path(sif).expanduser()

    try:
        start_sh_env = _parse_env_map(data.get("start_sh_env"), "start_sh_env")
    except ConfigValidationError as e:
        errors.extend(e.errors)
        start_sh_env = {}

    tg_raw = data.get("telegram")
    if tg_raw is None:
        tg_raw = {}
    if not isinstance(tg_raw, dict):
        err("telegram", "must be an object when set")
        tg_raw = {}

    bt_raw = tg_raw.get("bot_token")
    ci_raw = tg_raw.get("chat_id")
    bot_token: str | None = None
    chat_id: str | None = None
    if bt_raw is not None:
        if not isinstance(bt_raw, str) or not bt_raw.strip():
            err("telegram.bot_token", "must be a non-empty string when set")
        else:
            bot_token = bt_raw.strip()
    if ci_raw is not None:
        if isinstance(ci_raw, bool):
            err("telegram.chat_id", "must be a string or number when set")
        elif isinstance(ci_raw, int):
            chat_id = str(ci_raw)
        elif isinstance(ci_raw, str) and ci_raw.strip():
            chat_id = ci_raw.strip()
        else:
            err("telegram.chat_id", "must be a non-empty string or integer when set")

    bte = tg_raw.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    cie = tg_raw.get("chat_id_env", "TELEGRAM_CHAT_ID")
    if not isinstance(bte, str) or not bte.strip():
        err("telegram.bot_token_env", "must be a non-empty string")
        bte = "TELEGRAM_BOT_TOKEN"
    if not isinstance(cie, str) or not cie.strip():
        err("telegram.chat_id_env", "must be a non-empty string")
        cie = "TELEGRAM_CHAT_ID"
    bte = bte.strip()
    cie = cie.strip()

    bot_token, chat_id, bte, cie = _migrate_telegram_misplaced_keys(bot_token, chat_id, bte, cie)

    if bot_token is None and not _ENV_NAME.match(bte):
        err(
            "telegram.bot_token_env",
            "must be a valid environment variable name, or put the token in telegram.bot_token",
        )
    if chat_id is None and not _ENV_NAME.match(cie):
        err(
            "telegram.chat_id_env",
            "must be a valid environment variable name, or put the id in telegram.chat_id",
        )

    telegram = TelegramConfig(
        bot_token=bot_token,
        chat_id=chat_id,
        bot_token_env=bte,
        chat_id_env=cie,
    )

    repos_raw = data.get("repos")
    if not isinstance(repos_raw, list) or not repos_raw:
        err("repos", "must be a non-empty array")

    repos: list[RepoConfig] = []
    seen_names: set[str] = set()
    if isinstance(repos_raw, list):
        for i, item in enumerate(repos_raw):
            prefix = f"repos[{i}]"
            if not isinstance(item, dict):
                err(prefix, "must be an object")
                continue
            url = item.get("url")
            branch = item.get("branch")
            if not isinstance(url, str) or not url.strip():
                err(f"{prefix}.url", "must be a non-empty string")
                continue
            if not isinstance(branch, str) or not branch.strip():
                err(f"{prefix}.branch", "must be a non-empty string")
                continue
            url = url.strip()
            branch = branch.strip()
            if not _is_ssh_git_url(url):
                err(f"{prefix}.url", f"must be an SSH remote (git@host:path or ssh://...), got {url!r}")
                continue
            name_raw = item.get("name")
            if name_raw is None:
                try:
                    name = _derive_name_from_url(url)
                except ConfigValidationError as e:
                    errors.extend(e.errors)
                    continue
            else:
                if not isinstance(name_raw, str) or not name_raw.strip():
                    err(f"{prefix}.name", "must be a non-empty string when set")
                    continue
                name = name_raw.strip()
            if name in seen_names:
                err(f"{prefix}.name", f"duplicate repo name: {name!r}")
                continue
            seen_names.add(name)
            repo_ssh: Path | None = None
            if "ssh_identity_file" in item and item["ssh_identity_file"] is not None:
                rs = item["ssh_identity_file"]
                if not isinstance(rs, str) or not rs.strip():
                    err(f"{prefix}.ssh_identity_file", "must be a non-empty string when set")
                else:
                    repo_ssh = Path(rs).expanduser()
            try:
                repo_env = _parse_env_map(item.get("env"), f"{prefix}.env")
            except ConfigValidationError as e:
                errors.extend(e.errors)
                repo_env = {}
            repos.append(
                RepoConfig(name=name, url=url, branch=branch, ssh_identity_file=repo_ssh, env=repo_env)
            )

    if errors:
        raise ConfigValidationError(errors)

    return AppConfig(
        config_version=int(version),
        base_path=base,
        poll_interval_seconds=poll,
        state_file=state_file,
        start_sh_timeout_seconds=timeout,
        start_sh_failure_retry_attempts=retry_attempts,
        start_sh_failure_retry_interval_seconds=retry_interval,
        deploy_backoff_initial_seconds=backoff_initial,
        deploy_backoff_max_seconds=backoff_max,
        ssh_identity_file=ssh_identity,
        start_sh_env=start_sh_env,
        telegram=telegram,
        repos=tuple(repos),
    )


def load_config_dict(data: dict[str, Any]) -> AppConfig:
    migrated, _ = migrate(data)
    return validate_config(migrated)


def load_config(path: Path) -> AppConfig:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    data = parse_raw_text(raw, source=str(path))
    migrated, _ = migrate(data)
    return validate_config(migrated)


def load_config_with_warnings(path: Path) -> tuple[AppConfig, list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    data = parse_raw_text(raw, source=str(path))
    migrated, warnings = migrate(data)
    return validate_config(migrated), warnings


def config_fingerprint(cfg: AppConfig) -> str:
    parts = [
        str(cfg.config_version),
        str(cfg.base_path),
        str(cfg.poll_interval_seconds),
        str(cfg.state_file),
        str(cfg.start_sh_timeout_seconds),
        str(cfg.ssh_identity_file),
        json.dumps(dict(cfg.start_sh_env), sort_keys=True),
    ]
    for repo in cfg.repos:
        parts.append(
            "|".join(
                [
                    repo.name,
                    repo.url,
                    repo.branch,
                    str(repo.ssh_identity_file),
                    json.dumps(dict(repo.env), sort_keys=True),
                ]
            )
        )
    return "\n".join(parts)


def summarize_config_diff(old: AppConfig, new: AppConfig) -> str:
    old_names = {r.name for r in old.repos}
    new_names = {r.name for r in new.repos}
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    bits: list[str] = []
    if added:
        bits.append(f"added repos: {', '.join(added)}")
    if removed:
        bits.append(f"removed repos: {', '.join(removed)}")
    changed = []
    old_by_name = {r.name: r for r in old.repos}
    for r in new.repos:
        prev = old_by_name.get(r.name)
        if prev and (prev.url != r.url or prev.branch != r.branch or prev.env != r.env):
            changed.append(r.name)
    if changed:
        bits.append(f"changed repos: {', '.join(sorted(changed))}")
    if old.poll_interval_seconds != new.poll_interval_seconds:
        bits.append(f"poll_interval_seconds: {old.poll_interval_seconds} -> {new.poll_interval_seconds}")
    if old.start_sh_env != new.start_sh_env:
        bits.append("start_sh_env changed")
    if not bits:
        bits.append("no material changes")
    return "; ".join(bits)


def build_git_env(
    config: AppConfig,
    *,
    repo: RepoConfig | None = None,
    parent: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if parent is None else parent)
    if env.get("GIT_SSH_COMMAND"):
        return env

    identity_paths: list[Path] = []
    if repo is not None and repo.ssh_identity_file is not None:
        identity_paths.append(repo.ssh_identity_file)
    if config.ssh_identity_file is not None:
        identity_paths.append(config.ssh_identity_file)

    resolved_seen: set[str] = set()
    unique_paths: list[Path] = []
    for p in identity_paths:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in resolved_seen:
            continue
        resolved_seen.add(key)
        unique_paths.append(p)

    if not unique_paths:
        return env

    parts: list[str] = ["ssh"]
    for p in unique_paths:
        parts.extend(["-i", shlex.quote(str(p))])
    parts.extend(["-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"])
    env["GIT_SSH_COMMAND"] = " ".join(parts)
    return env


def build_start_sh_env(config: AppConfig, repo: RepoConfig) -> dict[str, str]:
    """Merge env for ``start.sh``: os.environ → global → repo → git → watcher vars."""
    env = dict(os.environ)
    env.update(config.start_sh_env)
    env.update(repo.env)
    env = build_git_env(config, repo=repo, parent=env)
    repo_path = config.base_path / repo.name
    try:
        root_s = str(repo_path.resolve())
    except OSError:
        root_s = str(repo_path)
    env["PWD"] = root_s
    env["GIT_DEPLOY_REPO_ROOT"] = root_s
    return env
