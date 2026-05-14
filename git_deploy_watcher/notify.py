from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE = 4096

_GIT_PHASE_LABEL: dict[str, str] = {
    "clone": "clone",
    "status": "status check",
    "rev-parse(before)": "read HEAD",
    "fetch/checkout/merge": "pull / merge",
    "rev-parse(after)": "read HEAD",
    "clean": "git clean -fdx",
    "post-clean": "still dirty after clean",
}


def _short_sha_prefix(sha: str | None) -> str | None:
    if not sha:
        return None
    s = sha.strip()
    if len(s) < 7:
        return None
    return s[:12]


def _first_meaningful_line(*blocks: str, limit: int = 220) -> str:
    for block in blocks:
        for raw in (block or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if len(line) > limit:
                return line[: limit - 1] + "…"
            return line
    return ""


def format_git_failure_alert(
    *,
    repo_name: str,
    branch: str,
    phase: str,
    exit_code: int | None,
    err_message: str,
    stderr: str,
    stdout: str,
    head_sha: str | None = None,
) -> str:
    label = _GIT_PHASE_LABEL.get(phase, phase.replace("_", " "))
    hint = _first_meaningful_line(stderr, stdout, err_message, limit=240)
    head = f"[git] {repo_name} · {branch}"
    short = _short_sha_prefix(head_sha)
    if short:
        head += f" · {short}"
    mid = f"{label} · exit {exit_code}" if exit_code is not None else f"{label} · failed"
    lines = [head, mid]
    if hint:
        lines.append(hint)
    return "\n".join(lines)


def format_start_failure_alert(
    *,
    repo_name: str,
    branch: str,
    head_sha: str,
    exit_code: int | None,
    err_message: str,
    stderr: str,
    stdout: str,
) -> str:
    raw = (head_sha or "").strip()
    short_sha = _short_sha_prefix(raw) or (raw[:12] if raw else "?")
    hint = _first_meaningful_line(stderr, stdout, err_message, limit=240)
    head = f"[deploy] {repo_name} · {branch} · {short_sha}"
    mid = f"start.sh · exit {exit_code}" if exit_code is not None else "start.sh · failed"
    if hint:
        return f"{head}\n{mid}\n{hint}".strip()
    return f"{head}\n{mid}".strip()


def truncate_telegram_message(text: str, max_len: int = TELEGRAM_MAX_MESSAGE) -> str:
    if len(text) <= max_len:
        return text
    suffix = "\n…(truncated)"
    keep = max_len - len(suffix)
    if keep < 1:
        return text[:max_len]
    return text[:keep] + suffix


@dataclass
class TelegramRateLimiter:
    """At most one notification per ``rate_key`` per ``window_seconds`` (e.g. ``api:git`` vs ``api:start.sh``)."""

    window_seconds: float = 300.0
    _last_sent: dict[str, float] = field(default_factory=dict)

    def allow(self, rate_key: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(rate_key)
        if last is not None and (now - last) < self.window_seconds:
            return False
        self._last_sent[rate_key] = now
        return True


def send_telegram_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    timeout: float = 30.0,
) -> None:
    body = {
        "chat_id": chat_id,
        "text": truncate_telegram_message(text),
        "disable_web_page_preview": True,
    }
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"Telegram HTTP {e.code}: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram request failed: {e}") from e

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Telegram invalid JSON: {raw[:500]}") from e
    if not parsed.get("ok"):
        raise RuntimeError(f"Telegram API error: {raw[:500]}")
