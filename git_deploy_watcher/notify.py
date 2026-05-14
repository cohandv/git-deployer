from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE = 4096


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
    """At most one notification per repo per window_seconds."""

    window_seconds: float = 300.0
    _last_sent: dict[str, float] = field(default_factory=dict)

    def allow(self, repo_name: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(repo_name)
        if last is not None and (now - last) < self.window_seconds:
            return False
        self._last_sent[repo_name] = now
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
