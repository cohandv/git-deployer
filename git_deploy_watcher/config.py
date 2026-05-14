from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from urllib.parse import urlparse
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram credentials: optional literals plus env fallbacks per field.

    For each of token and chat id, a non-empty **inline** value in config wins;
    otherwise the value is read from ``os.environ`` using ``*_env`` names.
    """

    bot_token: str | None
    chat_id: str | None
    bot_token_env: str
    chat_id_env: str


@dataclass(frozen=True)
class RepoConfig:
    name: str
    url: str
    branch: str


@dataclass(frozen=True)
class AppConfig:
    base_path: Path
    poll_interval_seconds: int
    state_file: Path
    start_sh_timeout_seconds: int
    start_sh_failure_retry_attempts: int
    start_sh_failure_retry_interval_seconds: int
    deploy_backoff_initial_seconds: int
    deploy_backoff_max_seconds: int
    ssh_identity_file: Path | None
    telegram: TelegramConfig
    repos: tuple[RepoConfig, ...]


_SCP_STYLE = re.compile(r"^git@[^:]+:.+$")
_SSH_URL = re.compile(r"^ssh://", re.IGNORECASE)


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
        raise ConfigError(f"cannot derive repo name from url: {url!r}")
    return base


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _looks_like_telegram_bot_token(value: str) -> bool:
    """Heuristic: BotFather issues ``<digits>:<secret>`` (secret is alphanumeric + _-)."""
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
    """If token was put in ``bot_token_env`` or chat id in ``chat_id_env``, fix it."""
    if bot_token is None and not _ENV_NAME.match(bte) and _looks_like_telegram_bot_token(bte):
        bot_token = bte.strip()
        bte = "TELEGRAM_BOT_TOKEN"
    if chat_id is None and not _ENV_NAME.match(cie) and re.fullmatch(r"-?\d{1,20}", cie):
        chat_id = cie.strip()
        cie = "TELEGRAM_CHAT_ID"
    return bot_token, chat_id, bte, cie


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("config root must be a JSON object")
    return data


def telegram_credentials(cfg: AppConfig) -> tuple[str | None, str | None]:
    """Resolve bot token and chat id (inline config overrides env per field)."""
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


def load_config(path: Path) -> AppConfig:
    data = _read_json(path)

    base_path = data.get("base_path")
    if not isinstance(base_path, str) or not base_path.strip():
        raise ConfigError("base_path must be a non-empty string")
    base = Path(base_path).expanduser()

    poll = data.get("poll_interval_seconds", 60)
    if not isinstance(poll, int) or poll < 1:
        raise ConfigError("poll_interval_seconds must be an integer >= 1")

    state_file_raw = data.get("state_file", "/var/lib/git-deploy-watcher/state.json")
    if not isinstance(state_file_raw, str) or not state_file_raw.strip():
        raise ConfigError("state_file must be a non-empty string")
    state_file = Path(state_file_raw).expanduser()

    timeout = data.get("start_sh_timeout_seconds", 300)
    if not isinstance(timeout, int) or timeout < 1:
        raise ConfigError("start_sh_timeout_seconds must be an integer >= 1")

    retry_attempts = data.get("start_sh_failure_retry_attempts", 5)
    if not isinstance(retry_attempts, int) or retry_attempts < 1:
        raise ConfigError("start_sh_failure_retry_attempts must be an integer >= 1")

    retry_interval = data.get("start_sh_failure_retry_interval_seconds", 10)
    if not isinstance(retry_interval, int) or retry_interval < 0:
        raise ConfigError("start_sh_failure_retry_interval_seconds must be an integer >= 0")

    backoff_initial = data.get("deploy_backoff_initial_seconds", 10)
    if not isinstance(backoff_initial, int) or backoff_initial < 1:
        raise ConfigError("deploy_backoff_initial_seconds must be an integer >= 1")

    backoff_max = data.get("deploy_backoff_max_seconds", 300)
    if not isinstance(backoff_max, int) or backoff_max < 1:
        raise ConfigError("deploy_backoff_max_seconds must be an integer >= 1")
    if backoff_max < backoff_initial:
        raise ConfigError("deploy_backoff_max_seconds must be >= deploy_backoff_initial_seconds")

    ssh_identity: Path | None = None
    if "ssh_identity_file" in data and data["ssh_identity_file"] is not None:
        sif = data["ssh_identity_file"]
        if not isinstance(sif, str) or not sif.strip():
            raise ConfigError("ssh_identity_file must be a non-empty string when set")
        ssh_identity = Path(sif).expanduser()

    tg_raw = data.get("telegram")
    if tg_raw is None:
        tg_raw = {}
    if not isinstance(tg_raw, dict):
        raise ConfigError("telegram must be an object when set")

    bt_raw = tg_raw.get("bot_token")
    ci_raw = tg_raw.get("chat_id")
    bot_token: str | None = None
    chat_id: str | None = None
    if bt_raw is not None:
        if not isinstance(bt_raw, str) or not bt_raw.strip():
            raise ConfigError("telegram.bot_token must be a non-empty string when set")
        bot_token = bt_raw.strip()
    if ci_raw is not None:
        if isinstance(ci_raw, bool):
            raise ConfigError("telegram.chat_id must be a string or number when set")
        if isinstance(ci_raw, int):
            chat_id = str(ci_raw)
        elif isinstance(ci_raw, str) and ci_raw.strip():
            chat_id = ci_raw.strip()
        else:
            raise ConfigError("telegram.chat_id must be a non-empty string or integer when set")

    bte = tg_raw.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    cie = tg_raw.get("chat_id_env", "TELEGRAM_CHAT_ID")
    if not isinstance(bte, str) or not bte.strip():
        raise ConfigError("telegram.bot_token_env must be a non-empty string")
    if not isinstance(cie, str) or not cie.strip():
        raise ConfigError("telegram.chat_id_env must be a non-empty string")
    bte = bte.strip()
    cie = cie.strip()

    bot_token, chat_id, bte, cie = _migrate_telegram_misplaced_keys(bot_token, chat_id, bte, cie)

    # Env var *names* must look like real env keys only when we actually read from the environment.
    if bot_token is None:
        if not _ENV_NAME.match(bte):
            raise ConfigError(
                "telegram.bot_token_env must be a valid environment variable name (e.g. TELEGRAM_BOT_TOKEN), "
                "or put the token in telegram.bot_token (not in bot_token_env)."
            )
    if chat_id is None:
        if not _ENV_NAME.match(cie):
            raise ConfigError(
                "telegram.chat_id_env must be a valid environment variable name (e.g. TELEGRAM_CHAT_ID), "
                "or put the id in telegram.chat_id (not in chat_id_env)."
            )

    telegram = TelegramConfig(
        bot_token=bot_token,
        chat_id=chat_id,
        bot_token_env=bte,
        chat_id_env=cie,
    )

    repos_raw = data.get("repos")
    if not isinstance(repos_raw, list) or not repos_raw:
        raise ConfigError("repos must be a non-empty array")

    repos: list[RepoConfig] = []
    seen_names: set[str] = set()
    for i, item in enumerate(repos_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"repos[{i}] must be an object")
        url = item.get("url")
        branch = item.get("branch")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"repos[{i}].url must be a non-empty string")
        if not isinstance(branch, str) or not branch.strip():
            raise ConfigError(f"repos[{i}].branch must be a non-empty string")
        url = url.strip()
        branch = branch.strip()
        if not _is_ssh_git_url(url):
            raise ConfigError(
                f"repos[{i}].url must be an SSH remote (git@host:path or ssh://...), got {url!r}"
            )
        name_raw = item.get("name")
        if name_raw is None:
            name = _derive_name_from_url(url)
        else:
            if not isinstance(name_raw, str) or not name_raw.strip():
                raise ConfigError(f"repos[{i}].name must be a non-empty string when set")
            name = name_raw.strip()
        if name in seen_names:
            raise ConfigError(f"duplicate repo name: {name!r}")
        seen_names.add(name)
        repos.append(RepoConfig(name=name, url=url, branch=branch))

    return AppConfig(
        base_path=base,
        poll_interval_seconds=poll,
        state_file=state_file,
        start_sh_timeout_seconds=timeout,
        start_sh_failure_retry_attempts=retry_attempts,
        start_sh_failure_retry_interval_seconds=retry_interval,
        deploy_backoff_initial_seconds=backoff_initial,
        deploy_backoff_max_seconds=backoff_max,
        ssh_identity_file=ssh_identity,
        telegram=telegram,
        repos=tuple(repos),
    )


def build_git_env(config: AppConfig, parent: dict[str, str] | None = None) -> dict[str, str]:
    """Merge parent env with GIT_SSH_COMMAND when ssh_identity_file is set.

    If ``GIT_SSH_COMMAND`` is already set in ``parent``/``os.environ``, it wins
    and ``ssh_identity_file`` is ignored (operators may set it in systemd
    ``EnvironmentFile``).
    """
    env = dict(os.environ if parent is None else parent)
    if env.get("GIT_SSH_COMMAND"):
        return env
    if config.ssh_identity_file is None:
        return env
    key = shlex.quote(str(config.ssh_identity_file))
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    )
    return env
