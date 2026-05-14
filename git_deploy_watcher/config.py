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

    timeout = data.get("start_sh_timeout_seconds", 3600)
    if not isinstance(timeout, int) or timeout < 1:
        raise ConfigError("start_sh_timeout_seconds must be an integer >= 1")

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
    bte = tg_raw.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    cie = tg_raw.get("chat_id_env", "TELEGRAM_CHAT_ID")
    if not isinstance(bte, str) or not bte.strip():
        raise ConfigError("telegram.bot_token_env must be a non-empty string")
    if not isinstance(cie, str) or not cie.strip():
        raise ConfigError("telegram.chat_id_env must be a non-empty string")
    telegram = TelegramConfig(bot_token_env=bte, chat_id_env=cie)

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
