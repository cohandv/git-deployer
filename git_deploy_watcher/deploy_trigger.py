from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DeployTriggerError(ValueError):
    pass


class _InProcessTriggers:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._wake = threading.Event()

    def add(self, repo_name: str) -> None:
        with self._lock:
            self._pending.add(repo_name)
        self._wake.set()

    def drain(self) -> set[str]:
        with self._lock:
            names = set(self._pending)
            self._pending.clear()
        with self._lock:
            if not self._pending:
                self._wake.clear()
        return names

    def wait(self, timeout: float) -> None:
        if timeout <= 0:
            return
        self._wake.wait(timeout=timeout)


_IN_PROCESS = _InProcessTriggers()


def triggers_dir(state_file: Path) -> Path:
    return state_file.parent / "triggers"


def _validate_repo_name(name: str) -> str:
    n = name.strip()
    if not n or not _REPO_NAME.match(n):
        raise DeployTriggerError(f"invalid repo name: {name!r}")
    return n


def request_deploy(state_file: Path, repo_name: str) -> None:
    """Queue an immediate pull + deploy for ``repo_name`` (works across processes)."""
    name = _validate_repo_name(repo_name)
    tdir = triggers_dir(state_file)
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / f"{name}.json"
    payload = {"repo": name, "requested_at": time.time()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    _IN_PROCESS.add(name)


def drain_triggers(state_file: Path) -> set[str]:
    """Return repo names with pending manual deploy requests and clear them."""
    names = _IN_PROCESS.drain()
    tdir = triggers_dir(state_file)
    if tdir.is_dir():
        for p in tdir.glob("*.json"):
            if p.name.startswith("."):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                repo = data.get("repo") if isinstance(data, dict) else p.stem
                if isinstance(repo, str) and repo.strip():
                    names.add(repo.strip())
            except (OSError, json.JSONDecodeError):
                names.add(p.stem)
            try:
                p.unlink()
            except OSError:
                pass
    return names


def peek_pending(state_file: Path) -> bool:
    with _IN_PROCESS._lock:
        if _IN_PROCESS._pending:
            return True
    tdir = triggers_dir(state_file)
    if not tdir.is_dir():
        return False
    return any(p.suffix == ".json" and not p.name.startswith(".") for p in tdir.iterdir())


def wait_or_timeout(state_file: Path, timeout: float) -> None:
    """Sleep up to ``timeout`` seconds, waking early when a deploy is queued."""
    deadline = time.monotonic() + timeout
    while True:
        if peek_pending(state_file):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        _IN_PROCESS.wait(min(1.0, remaining))
