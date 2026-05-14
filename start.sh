#!/usr/bin/env bash
set -euo pipefail

# Runs as the same user as git-deploy-watcher (no TTY). Do not use `sudo` here—it
# cannot prompt for a password and will fail in the service.
#
# Common patterns without sudo:
# - Docker: add the service user to the `docker` group (or use rootless Docker).
# - systemctl: polkit must allow this user — see README “systemctl restart from start.sh”
#   and polkit/99-git-deploy-manage-units.rules.example (avoids “Interactive authentication required”).
# - Files: write only under directories owned by that user.

# Restart this service from a repo the watcher deploys: defer so the watcher can finish the
# current tick and write state_file before systemd stops the process (immediate restart can
# prevent saving last_ok and cause a deploy/restart loop). Requires polkit — see README.
( sleep 2; exec systemctl restart git-deploy-watcher ) </dev/null >/dev/null 2>&1 &
