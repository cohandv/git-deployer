from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from git_deploy_watcher.config import (
    ConfigError,
    ConfigValidationError,
    load_config,
    load_config_dict,
    load_config_with_warnings,
)
from git_deploy_watcher.config_migrate import canonical_json, migrate, parse_raw_text
from git_deploy_watcher.config_store import diff_configs, list_history, load_history, save_config
from git_deploy_watcher.deploy_trigger import DeployTriggerError, request_deploy


def _read_json_arg(path: Path | None, raw: str | None) -> dict[str, Any]:
    if path is not None:
        text = path.read_text(encoding="utf-8")
    elif raw is not None:
        text = raw
    else:
        text = sys.stdin.read()
    return parse_raw_text(text, source=str(path) if path else "stdin")


def _api_json(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"errors": [{"path": "", "message": raw or str(e)}]}
        parsed["_http_status"] = e.code
        return parsed
    if not raw:
        return {}
    return json.loads(raw)


def _cmd_config_show(args: argparse.Namespace) -> int:
    _, warnings = load_config_with_warnings(args.config)
    data = parse_raw_text(args.config.read_text(encoding="utf-8"), source=str(args.config))
    migrated, _ = migrate(data)
    sys.stdout.write(canonical_json(migrated))
    if warnings and args.verbose:
        print("\n# migration warnings: " + "; ".join(warnings), file=sys.stderr)
    return 0


def _cmd_config_validate(args: argparse.Namespace) -> int:
    try:
        data = _read_json_arg(args.file, args.json)
        migrated, warnings = migrate(data)
        load_config_dict(migrated)
    except (ConfigError, ConfigValidationError) as e:
        print(str(e), file=sys.stderr)
        return 1
    if warnings:
        print("warnings: " + "; ".join(warnings), file=sys.stderr)
    print("ok")
    return 0


def _cmd_config_apply(args: argparse.Namespace) -> int:
    try:
        data = _read_json_arg(args.file, args.json)
        load_config_dict(data)
        save_config(args.config, data)
    except (ConfigError, ConfigValidationError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print("saved", args.config)
    if args.deploy:
        return _deploy_repos(args, args.deploy)
    return 0


def _cmd_history_list(args: argparse.Namespace) -> int:
    for item in list_history(args.config):
        print(f"{item['id']}\t{item['mtime']}")
    return 0


def _cmd_history_diff(args: argparse.Namespace) -> int:
    try:
        a = load_history(args.config, args.from_id)
        b = load_history(args.config, args.to_id)
        a_m, _ = migrate(a)
        b_m, _ = migrate(b)
        sys.stdout.write(diff_configs(a_m, b_m, from_label=args.from_id, to_label=args.to_id))
    except (ConfigError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def _deploy_repos(args: argparse.Namespace, names: list[str]) -> int:
    if args.api_url:
        base = args.api_url.rstrip("/")
        rc = 0
        for name in names:
            resp = _api_json("POST", f"{base}/api/repos/{name}/deploy")
            if resp.get("ok"):
                print(f"deploy queued: {name}")
            else:
                rc = 1
                err = resp.get("errors") or [{"message": str(resp)}]
                print(f"{name}: {err[0].get('message', err)}", file=sys.stderr)
        return rc
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    known = {r.name for r in cfg.repos}
    rc = 0
    for name in names:
        if name not in known:
            print(f"unknown repo: {name}", file=sys.stderr)
            rc = 1
            continue
        try:
            request_deploy(cfg.state_file, name)
            print(f"deploy queued: {name}")
        except DeployTriggerError as e:
            print(str(e), file=sys.stderr)
            rc = 1
    return rc


def _cmd_repo_deploy(args: argparse.Namespace) -> int:
    return _deploy_repos(args, [args.name])


def _cmd_api_config_get(args: argparse.Namespace) -> int:
    resp = _api_json("GET", f"{args.api_url.rstrip('/')}/api/config")
    if resp.get("config") is None:
        print(resp.get("errors", resp), file=sys.stderr)
        return 1
    sys.stdout.write(canonical_json(resp["config"]))
    return 0


def _cmd_api_config_post(args: argparse.Namespace) -> int:
    data = _read_json_arg(args.file, args.json)
    url = f"{args.api_url.rstrip('/')}/api/config"
    if args.deploy:
        url += "?" + "&".join(f"deploy={n}" for n in args.deploy)
    resp = _api_json("POST", url, data)
    if not resp.get("ok"):
        print(resp.get("errors", resp), file=sys.stderr)
        return 1
    print("saved")
    for name in resp.get("deploy_queued") or []:
        print(f"deploy queued: {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="git-deploy-admin",
        description="Manage git-deploy-watcher config and trigger deploys (file-based or HTTP API).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/git-deployer/config.json"),
        help="Path to config.json (default: /etc/git-deployer/config.json)",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="Admin HTTP base URL (e.g. http://127.0.0.1:8765). Used for deploy when set.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="Print current config JSON (migrated)")
    p_show.set_defaults(func=_cmd_config_show)

    p_val = sub.add_parser("validate", help="Validate JSON config from file or stdin")
    p_val.add_argument("-f", "--file", type=Path, default=None)
    p_val.add_argument("--json", type=str, default=None)
    p_val.set_defaults(func=_cmd_config_validate)

    p_apply = sub.add_parser("apply", help="Validate and save config JSON")
    p_apply.add_argument("-f", "--file", type=Path, default=None)
    p_apply.add_argument("--json", type=str, default=None)
    p_apply.add_argument(
        "--deploy",
        nargs="+",
        metavar="REPO",
        help="Queue immediate pull+deploy for these repos after save",
    )
    p_apply.set_defaults(func=_cmd_config_apply)

    p_hist = sub.add_parser("history", help="Config history commands")
    hist_sub = p_hist.add_subparsers(dest="history_cmd", required=True)
    p_hist_list = hist_sub.add_parser("list", help="List history snapshot ids")
    p_hist_list.set_defaults(func=_cmd_history_list)
    p_hist_diff = hist_sub.add_parser("diff", help="Diff two snapshots")
    p_hist_diff.add_argument("--from", dest="from_id", required=True)
    p_hist_diff.add_argument("--to", dest="to_id", default="current")
    p_hist_diff.set_defaults(func=_cmd_history_diff)

    p_dep = sub.add_parser("deploy", help="Queue immediate pull+deploy for one repo")
    p_dep.add_argument("name", help="Repo name from config")
    p_dep.set_defaults(func=_cmd_repo_deploy)

    p_api = sub.add_parser("api", help="Call a running admin HTTP server")
    api_sub = p_api.add_subparsers(dest="api_cmd", required=True)
    p_api_get = api_sub.add_parser("get", help="GET /api/config")
    p_api_get.add_argument("--url", dest="api_url", required=True)
    p_api_get.set_defaults(func=_cmd_api_config_get)
    p_api_post = api_sub.add_parser("post", help="POST /api/config")
    p_api_post.add_argument("--url", dest="api_url", required=True)
    p_api_post.add_argument("-f", "--file", type=Path, default=None)
    p_api_post.add_argument("--json", type=str, default=None)
    p_api_post.add_argument("--deploy", nargs="+", metavar="REPO", default=None)
    p_api_post.set_defaults(func=_cmd_api_config_post)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
