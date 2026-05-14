# git-deploy-watcher

Ubuntu-oriented service that **polls multiple Git repositories over SSH**, detects when the configured branch advances, runs each repository’s root **`start.sh`**, persists the last successful deploy revision, and sends **Telegram** alerts when `start.sh` fails.

Git operations use the system **`git`** CLI. Remotes must be **SSH** (`git@host:path` or `ssh://…`). HTTPS is rejected at config load time.

## What you need on each application repo

- A **`start.sh`** at the repository root (tracked in Git).
- The script should be **idempotent** when possible; the watcher may run it after every new revision.

## Configuration

Copy [`config.example.json`](config.example.json) to `/etc/git-deployer/config.json` and edit:

| Field | Meaning |
|--------|--------|
| `base_path` | Parent directory for checkouts (`{base_path}/{name}/`). |
| `poll_interval_seconds` | Sleep between full scans (default `60`). |
| `state_file` | JSON file storing last **successful** deploy SHA per repo. |
| `start_sh_timeout_seconds` | Timeout for `start.sh` (default `3600`). |
| `ssh_identity_file` | Optional path to a **private key** used for all `git` calls via `GIT_SSH_COMMAND`. |
| `telegram` | Optional object; if omitted, defaults apply for the two fields below. |
| `telegram.bot_token_env` | Name of env var holding the Telegram bot token (default `TELEGRAM_BOT_TOKEN`). |
| `telegram.chat_id_env` | Name of env var holding the destination chat id (default `TELEGRAM_CHAT_ID`). |
| `repos[]` | Each entry: `name` (optional), `url` (SSH only), `branch`. |

### SSH identity precedence

1. If **`GIT_SSH_COMMAND`** is already set in the process environment (for example in `secrets.env` via systemd), it is **left unchanged** and `ssh_identity_file` is ignored.
2. Otherwise, if **`ssh_identity_file`** is set in JSON, the watcher sets  
   `GIT_SSH_COMMAND='ssh -i <path> -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'`.
3. If neither applies, OpenSSH defaults for the service user apply (`~/.ssh/config`, agent, default key filenames).

### Telegram secrets

Do **not** put the bot token in `config.json`. Use `/etc/git-deployer/secrets.env` (mode `600`), referenced from systemd:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

Optional: set `GIT_SSH_COMMAND=…` there instead of using `ssh_identity_file`.

## Install on Ubuntu (22.04 / 24.04)

### 1. Packages

```bash
sudo apt update
sudo apt install -y git python3 python3-pip
```

### 2. Service user

```bash
sudo useradd --system --home /var/lib/git-deploy-watcher --shell /usr/sbin/nologin git-deploy
```

### 3. Directories

```bash
sudo mkdir -p /etc/git-deployer /etc/git-deployer/ssh /var/deploy/apps /var/lib/git-deploy-watcher/locks
sudo chown -R git-deploy:git-deploy /var/deploy/apps /var/lib/git-deploy-watcher
sudo chmod 755 /etc/git-deployer
```

### 4. Install this project

From a checkout of this repository on the server:

```bash
cd /path/to/git-deployer
sudo pip3 install --break-system-packages .
```

Ubuntu 24.04 often needs `--break-system-packages` for system-wide `pip`. Alternatively install into a venv owned by `git-deploy` and point `ExecStart` at that venv’s `python`.

Install a small wrapper so systemd can call a stable path:

```bash
sudo tee /usr/local/bin/git-deploy-watcher >/dev/null <<'EOF'
#!/bin/sh
exec python3 -m git_deploy_watcher --config /etc/git-deployer/config.json
EOF
sudo chmod +x /usr/local/bin/git-deploy-watcher
```

`python3 -m git_deploy_watcher` uses whichever environment has the package (PEP 668–safe approach: install with `pip install --user` as `git-deploy` and ensure `PATH` includes `~/.local/bin`, or use a venv and point `ExecStart` to the venv’s `python` running `-m git_deploy_watcher`).

### 5. Configuration files

```bash
sudo cp config.example.json /etc/git-deployer/config.json
sudo nano /etc/git-deployer/config.json   # set base_path, repos (SSH URLs), branches
```

### 6. SSH deploy key

1. Generate a key **without a passphrase** for automation (example):

   ```bash
   sudo ssh-keygen -t ed25519 -f /etc/git-deployer/ssh/id_ed25519 -N "" -C "git-deploy-watcher"
   ```

2. Register the **public** key (`id_ed25519.pub`) as a **read-only** deploy key (GitHub, GitLab, Gitea, etc.).

3. Lock down permissions:

   ```bash
   sudo chown root:git-deploy /etc/git-deployer/ssh/id_ed25519
   sudo chmod 640 /etc/git-deployer/ssh/id_ed25519
   ```

   The service user must be able to read the key; `640` with group `git-deploy` satisfies that.

4. **Host keys**: either pre-populate `~git-deploy/.ssh/known_hosts` using `sudo -u git-deploy ssh-keyscan github.com >> ~git-deploy/.ssh/known_hosts`, or rely on `StrictHostKeyChecking=accept-new` from the generated `GIT_SSH_COMMAND` when using `ssh_identity_file`.

5. Point `ssh_identity_file` in `config.json` at `/etc/git-deployer/ssh/id_ed25519`, **or** configure `~git-deploy/.ssh/config` instead and omit `ssh_identity_file`.

### 7. Telegram `secrets.env`

```bash
sudo install -m 600 /dev/null /etc/git-deployer/secrets.env
sudo nano /etc/git-deployer/secrets.env
```

Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Optionally add `GIT_SSH_COMMAND`.

### 8. systemd

```bash
sudo cp systemd/git-deploy-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now git-deploy-watcher
```

### 9. Verify

```bash
systemctl status git-deploy-watcher
journalctl -u git-deploy-watcher -f
```

Confirm Git + SSH as the service user (use one of your SSH repo URLs):

```bash
sudo -u git-deploy git ls-remote git@github.com:org/repo.git HEAD
```

### 10. Telegram bot (short)

1. Talk to [@BotFather](https://t.me/BotFather), create a bot, copy the **token** into `TELEGRAM_BOT_TOKEN`.
2. Obtain your **chat id** (DM with [@userinfobot](https://t.me/userinfobot) or `getUpdates` after messaging your bot) and set `TELEGRAM_CHAT_ID`.

## Behavior summary

- **Clone** if `{base_path}/{name}` is missing (`git clone --branch … --single-branch`).
- **Update** with `git fetch origin`, `git checkout <branch>`, `git merge --ff-only origin/<branch>` (non-fast-forward stays logged; no Telegram for git errors).
- **Dirty tree**: logs a warning; does not invent a new revision.
- **Deploy**: runs `/bin/bash start.sh` in the repo root when the current `HEAD` is not yet recorded as successfully deployed.
- **State**: after a **successful** `start.sh`, the current `HEAD` SHA is written to `state_file`. Failures keep the old entry so the next poll retries.
- **Telegram**: only on **`start.sh` non-zero**; messages are truncated to Telegram’s length limit; optional **rate limit** (one alert per repo per 5 minutes).

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 -m unittest discover -s tests -v
```

## License

Specify your license in a `LICENSE` file as needed.
