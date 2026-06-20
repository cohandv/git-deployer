from __future__ import annotations

import difflib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git_deploy_watcher.config_migrate import canonical_json, migrate, parse_raw_text

HISTORY_DIR_NAME = "config.history"
HISTORY_MAX_SNAPSHOTS = 50


def history_dir(config_path: Path) -> Path:
    return config_path.parent / HISTORY_DIR_NAME


def _history_id_from_mtime(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _snapshot_path(config_path: Path, snapshot_id: str) -> Path:
    return history_dir(config_path) / f"{snapshot_id}.json"


def _rotate_history(config_path: Path) -> None:
    hdir = history_dir(config_path)
    if not hdir.is_dir():
        return
    entries = sorted(hdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in entries[HISTORY_MAX_SNAPSHOTS:]:
        try:
            old.unlink()
        except OSError:
            pass


def _archive_current(config_path: Path) -> None:
    if not config_path.is_file():
        return
    hdir = history_dir(config_path)
    hdir.mkdir(parents=True, exist_ok=True)
    mtime = config_path.stat().st_mtime
    snapshot_id = _history_id_from_mtime(mtime)
    dest = _snapshot_path(config_path, snapshot_id)
    if dest.exists():
        snapshot_id = f"{snapshot_id[:-1]}{int(mtime * 1000) % 1000:03d}Z"
        dest = _snapshot_path(config_path, snapshot_id)
    dest.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    _rotate_history(config_path)


def save_config(config_path: Path, data: dict[str, Any]) -> None:
    """Atomically write config JSON, archiving the previous file to history."""
    migrated, _ = migrate(data)
    text = canonical_json(migrated)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _archive_current(config_path)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    with open(tmp, "rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, config_path)
    with open(config_path, "rb") as fh:
        os.fsync(fh.fileno())


def list_history(config_path: Path) -> list[dict[str, Any]]:
    hdir = history_dir(config_path)
    if not hdir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(hdir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        sid = p.stem
        out.append({"id": sid, "mtime": p.stat().st_mtime})
    return out


def load_history(config_path: Path, snapshot_id: str) -> dict[str, Any]:
    if snapshot_id == "current":
        if not config_path.is_file():
            raise FileNotFoundError(f"no current config at {config_path}")
        return parse_raw_text(config_path.read_text(encoding="utf-8"), source=str(config_path))
    path = _snapshot_path(config_path, snapshot_id)
    if not path.is_file():
        raise FileNotFoundError(f"history snapshot not found: {snapshot_id}")
    return parse_raw_text(path.read_text(encoding="utf-8"), source=str(path))


def diff_configs(a: dict[str, Any], b: dict[str, Any], *, from_label: str = "a", to_label: str = "b") -> str:
    a_text = canonical_json(a).splitlines(keepends=True)
    b_text = canonical_json(b).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(a_text, b_text, fromfile=from_label, tofile=to_label)
    )
