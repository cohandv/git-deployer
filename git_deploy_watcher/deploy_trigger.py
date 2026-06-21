from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MODE_DEPLOY = "deploy"
_MODE_SYNC = "sync"


class DeployTriggerError(ValueError):
    pass


@dataclass(frozen=True)
class TriggerBatch:
    deploy: frozenset[str]
    sync: frozenset[str]

    @property
    def all_repos(self) -> frozenset[str]:
        return self.deploy | self.sync


class _InProcessTriggers:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, str] = {}
        self._wake = threading.Event()

    def add(self, repo_name: str, *, mode: str) -> None:
        with self._lock:
            existing = self._pending.get(repo_name)
            if existing == _MODE_DEPLOY:
                return
            if mode == _MODE_DEPLOY or existing is None:
                self._pending[repo_name] = mode
        self._wake.set()

    def drain(self) -> TriggerBatch:
        with self._lock:
            pending = dict(self._pending)
            self._pending.clear()
        deploy: set[str] = set()
        sync: set[str] = set()
        for name, mode in pending.items():
            if mode == _MODE_SYNC:
                sync.add(name)
            else:
                deploy.add(name)
        with self._lock:
            if not self._pending:
                self._wake.clear()
        return TriggerBatch(deploy=frozenset(deploy), sync=frozenset(sync))


_IN_PROCESS = _InProcessTriggers()


def triggers_dir(state_file: Path) -> Path:
    return state_file.parent / "triggers"


def _validate_repo_name(name: str) -> str:
    n = name.strip()
    if not n or not _REPO_NAME.match(n):
        raise DeployTriggerError(f"invalid repo name: {name!r}")
    return n


def _validate_mode(mode: str) -> str:
    m = mode.strip().lower()
    if m not in (_MODE_DEPLOY, _MODE_SYNC):
        raise DeployTriggerError(f"invalid trigger mode: {mode!r}")
    return m


def _write_trigger_file(state_file: Path, repo_name: str, mode: str) -> None:
    tdir = triggers_dir(state_file)
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / f"{repo_name}.json"
    payload = {"repo": repo_name, "mode": mode, "requested_at": time.time()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def request_deploy(state_file: Path, repo_name: str) -> None:
    """Queue an immediate pull + deploy for ``repo_name`` (works across processes)."""
    name = _validate_repo_name(repo_name)
    _write_trigger_file(state_file, name, _MODE_DEPLOY)
    _IN_PROCESS.add(name, mode=_MODE_DEPLOY)


def request_sync(state_file: Path, repo_name: str) -> None:
    """Queue an immediate git fetch/merge for ``repo_name`` (no start.sh)."""
    name = _validate_repo_name(repo_name)
    _write_trigger_file(state_file, name, _MODE_SYNC)
    _IN_PROCESS.add(name, mode=_MODE_SYNC)


def _read_trigger_file(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            repo = data.get("repo")
            mode = data.get("mode", _MODE_DEPLOY)
            if isinstance(repo, str) and repo.strip():
                return repo.strip(), _validate_mode(str(mode))
    except (OSError, json.JSONDecodeError, DeployTriggerError):
        pass
    return path.stem, _MODE_DEPLOY


def drain_triggers(state_file: Path) -> TriggerBatch:
    """Return pending manual repo requests and clear them."""
    batch = _IN_PROCESS.drain()
    deploy = set(batch.deploy)
    sync = set(batch.sync)
    tdir = triggers_dir(state_file)
    if tdir.is_dir():
        for p in tdir.glob("*.json"):
            if p.name.startswith("."):
                continue
            repo, mode = _read_trigger_file(p)
            if mode == _MODE_SYNC:
                if repo not in deploy:
                    sync.add(repo)
            else:
                sync.discard(repo)
                deploy.add(repo)
            try:
                p.unlink()
            except OSError:
                pass
    return TriggerBatch(deploy=frozenset(deploy), sync=frozenset(sync))


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
