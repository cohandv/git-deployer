from __future__ import annotations

import copy
import json
from typing import Any

CURRENT_CONFIG_VERSION = 2


class ConfigError(ValueError):
    pass


def canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def parse_raw_text(raw: str, *, source: str = "config") -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"invalid JSON in {source}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("config root must be a JSON object")
    return data


def _detect_version(data: dict[str, Any]) -> int:
    version = data.get("config_version")
    if version is None:
        return 1
    if not isinstance(version, int):
        raise ConfigError("config_version must be an integer when set")
    return version


def _migrate_v1_to_v2(data: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    out = copy.deepcopy(data)
    if "start_sh_env" not in out:
        out["start_sh_env"] = {}
        warnings.append("added default start_sh_env")
    repos = out.get("repos")
    if isinstance(repos, list):
        for i, item in enumerate(repos):
            if isinstance(item, dict) and "env" not in item:
                item["env"] = {}
    out["config_version"] = 2
    return out


def migrate(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Upgrade config dict to CURRENT_CONFIG_VERSION."""
    warnings: list[str] = []
    version = _detect_version(data)
    if version > CURRENT_CONFIG_VERSION:
        raise ConfigError(
            f"unsupported config_version {version} (supported: 1–{CURRENT_CONFIG_VERSION})"
        )
    out = copy.deepcopy(data)
    while version < CURRENT_CONFIG_VERSION:
        if version == 1:
            out = _migrate_v1_to_v2(out, warnings)
            version = 2
        else:
            raise ConfigError(f"no migration path from config_version {version}")
    out["config_version"] = CURRENT_CONFIG_VERSION
    return out, warnings
