from __future__ import annotations

import json
import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from git_deploy_watcher.config import (
    ConfigError,
    ConfigValidationError,
    load_config,
    load_config_dict,
)
from git_deploy_watcher.config_migrate import CURRENT_CONFIG_VERSION, migrate, parse_raw_text
from git_deploy_watcher.config_store import diff_configs, list_history, load_history, save_config
from git_deploy_watcher.deploy_trigger import DeployTriggerError, request_deploy

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_SCHEMA_V2: dict[str, Any] = {
    "config_version": CURRENT_CONFIG_VERSION,
    "fields": {
        "base_path": {"type": "string", "required": True},
        "poll_interval_seconds": {"type": "integer", "default": 60},
        "state_file": {"type": "string", "default": "/var/lib/git-deploy-watcher/state.json"},
        "start_sh_timeout_seconds": {"type": "integer", "default": 300},
        "start_sh_failure_retry_attempts": {"type": "integer", "default": 5},
        "start_sh_failure_retry_interval_seconds": {"type": "integer", "default": 10},
        "deploy_backoff_initial_seconds": {"type": "integer", "default": 10},
        "deploy_backoff_max_seconds": {"type": "integer", "default": 300},
        "ssh_identity_file": {"type": "string", "optional": True},
        "start_sh_env": {"type": "object", "values": "string"},
        "telegram": {
            "type": "object",
            "fields": {
                "bot_token": {"type": "string", "optional": True, "sensitive": True},
                "chat_id": {"type": "string", "optional": True},
                "bot_token_env": {"type": "string", "default": "TELEGRAM_BOT_TOKEN"},
                "chat_id_env": {"type": "string", "default": "TELEGRAM_CHAT_ID"},
            },
        },
        "repos": {
            "type": "array",
            "minItems": 1,
            "itemFields": {
                "name": {"type": "string", "optional": True},
                "url": {"type": "string", "required": True},
                "branch": {"type": "string", "required": True},
                "ssh_identity_file": {"type": "string", "optional": True},
                "env": {"type": "object", "values": "string"},
            },
        },
    },
}


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return b""
    return handler.rfile.read(length)


def _queue_deploys(config_path: Path, repo_names: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        return [], [{"path": "", "message": str(e)}]
    known = {r.name for r in cfg.repos}
    queued: list[str] = []
    errors: list[dict[str, str]] = []
    for raw in repo_names:
        name = raw.strip()
        if not name:
            continue
        if name not in known:
            errors.append({"path": "repo", "message": f"unknown repo: {name!r}"})
            continue
        try:
            request_deploy(cfg.state_file, name)
            queued.append(name)
        except DeployTriggerError as e:
            errors.append({"path": "repo", "message": str(e)})
    return queued, errors


def _parse_deploy_query(query: dict[str, list[str]]) -> list[str]:
    return [v for v in query.get("deploy", []) if v.strip()]


def _config_to_api_dict(cfg_path: Path) -> dict[str, Any]:
    raw = cfg_path.read_text(encoding="utf-8")
    data = parse_raw_text(raw, source=str(cfg_path))
    migrated, warnings = migrate(data)
    return {"config": migrated, "warnings": warnings}


class AdminHandler(BaseHTTPRequestHandler):
    config_path: Path

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("admin %s - " + fmt, self.address_string(), *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            name = path[len("/static/") :]
            self._serve_static(name)
            return
        if path == "/api/config":
            self._get_config()
            return
        if path == "/api/schema":
            _json_response(self, 200, _SCHEMA_V2)
            return
        if path == "/api/history":
            self._get_history()
            return
        if path.startswith("/api/history/"):
            snapshot_id = path[len("/api/history/") :]
            self._get_history_item(snapshot_id)
            return
        if path == "/api/diff":
            self._get_diff(parse_qs(parsed.query))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self._post_config(parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/repos/") and parsed.path.endswith("/deploy"):
            repo_name = parsed.path[len("/api/repos/") : -len("/deploy")]
            self._post_repo_deploy(repo_name)
            return
        self.send_error(404)

    def _serve_static(self, name: str) -> None:
        safe = Path(name).name
        file_path = _STATIC_DIR / safe
        if not file_path.is_file():
            self.send_error(404)
            return
        content = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _get_config(self) -> None:
        try:
            payload = _config_to_api_dict(self.config_path)
            migrated = payload["config"]
            try:
                load_config_dict(migrated)
                validation_ok = True
                errors: list[dict[str, str]] = []
            except ConfigValidationError as e:
                validation_ok = False
                errors = [{"path": x.path, "message": x.message} for x in e.errors]
            except ConfigError as e:
                validation_ok = False
                errors = [{"path": "", "message": str(e)}]
            _json_response(
                self,
                200,
                {
                    "validation_ok": validation_ok,
                    "errors": errors,
                    "warnings": payload["warnings"],
                    "config": migrated,
                },
            )
        except ConfigError as e:
            _json_response(
                self,
                200,
                {"validation_ok": False, "errors": [{"path": "", "message": str(e)}], "config": None},
            )
        except OSError as e:
            _json_response(self, 500, {"ok": False, "errors": [{"path": "", "message": str(e)}]})

    def _post_config(self, query: dict[str, list[str]]) -> None:
        try:
            raw = _read_body(self)
            data = parse_raw_text(raw.decode("utf-8"), source="request body")
        except (UnicodeDecodeError, ConfigError) as e:
            _json_response(self, 400, {"ok": False, "errors": [{"path": "", "message": str(e)}]})
            return
        try:
            merged = self._merge_sensitive_fields(data)
            load_config_dict(merged)
            save_config(self.config_path, merged)
            migrated, warnings = migrate(merged)
            deploy_names = _parse_deploy_query(query)
            queued, deploy_errors = _queue_deploys(self.config_path, deploy_names)
            if deploy_errors and not queued:
                _json_response(self, 400, {"ok": False, "errors": deploy_errors})
                return
            body: dict[str, Any] = {
                "ok": True,
                "config_version": migrated.get("config_version"),
                "warnings": warnings,
            }
            if queued:
                body["deploy_queued"] = queued
            if deploy_errors:
                body["deploy_errors"] = deploy_errors
            _json_response(self, 200, body)
        except ConfigValidationError as e:
            _json_response(
                self,
                400,
                {"ok": False, "errors": [{"path": x.path, "message": x.message} for x in e.errors]},
            )
        except ConfigError as e:
            _json_response(self, 400, {"ok": False, "errors": [{"path": "", "message": str(e)}]})
        except OSError as e:
            _json_response(self, 500, {"ok": False, "errors": [{"path": "", "message": str(e)}]})

    def _post_repo_deploy(self, repo_name: str) -> None:
        from urllib.parse import unquote

        name = unquote(repo_name).strip()
        queued, errors = _queue_deploys(self.config_path, [name])
        if errors and not queued:
            _json_response(self, 400, {"ok": False, "errors": errors})
            return
        _json_response(self, 200, {"ok": True, "deploy_queued": queued})

    def _merge_sensitive_fields(self, incoming: dict[str, Any]) -> dict[str, Any]:
        if not self.config_path.is_file():
            return incoming
        try:
            current_raw = self.config_path.read_text(encoding="utf-8")
            current = parse_raw_text(current_raw, source=str(self.config_path))
        except (OSError, ConfigError):
            return incoming
        inc_tg = incoming.get("telegram")
        cur_tg = current.get("telegram")
        if not isinstance(inc_tg, dict) or not isinstance(cur_tg, dict):
            return incoming
        token = inc_tg.get("bot_token")
        if token in (None, "", "********"):
            if "bot_token" in cur_tg:
                inc_tg = dict(inc_tg)
                inc_tg["bot_token"] = cur_tg["bot_token"]
                incoming = dict(incoming)
                incoming["telegram"] = inc_tg
        return incoming

    def _get_history(self) -> None:
        try:
            items = list_history(self.config_path)
            _json_response(self, 200, {"history": items})
        except OSError as e:
            _json_response(self, 500, {"ok": False, "errors": [{"path": "", "message": str(e)}]})

    def _get_history_item(self, snapshot_id: str) -> None:
        try:
            data = load_history(self.config_path, snapshot_id)
            migrated, warnings = migrate(data)
            _json_response(self, 200, {"id": snapshot_id, "config": migrated, "warnings": warnings})
        except FileNotFoundError:
            self.send_error(404)
        except ConfigError as e:
            _json_response(self, 400, {"ok": False, "errors": [{"path": "", "message": str(e)}]})

    def _get_diff(self, query: dict[str, list[str]]) -> None:
        from_id = (query.get("from") or [""])[0]
        to_id = (query.get("to") or ["current"])[0]
        if not from_id:
            _json_response(self, 400, {"ok": False, "errors": [{"path": "from", "message": "required"}]})
            return
        try:
            a = load_history(self.config_path, from_id)
            b = load_history(self.config_path, to_id)
            a_m, _ = migrate(a)
            b_m, _ = migrate(b)
            text = diff_configs(a_m, b_m, from_label=from_id, to_label=to_id)
            _json_response(self, 200, {"diff": text})
        except FileNotFoundError as e:
            _json_response(self, 404, {"ok": False, "errors": [{"path": "", "message": str(e)}]})
        except ConfigError as e:
            _json_response(self, 400, {"ok": False, "errors": [{"path": "", "message": str(e)}]})


def start_admin_server(config_path: Path, *, host: str, port: int) -> ThreadingHTTPServer:
    handler_cls = type(
        "BoundAdminHandler",
        (AdminHandler,),
        {"config_path": config_path.resolve()},
    )
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, name="admin-http", daemon=True)
    thread.start()
    return server
