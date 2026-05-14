# git-deploy-watcher

Ubuntu-oriented service that **polls multiple Git repositories over SSH**, detects when the configured branch advances, runs each repository’s root **`start.sh`**, persists the last successful deploy revision, and sends **Telegram** alerts when **`start.sh` fails** or **git** operations fail (clone / fetch / merge / etc.).

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
| `telegram` | Optional object; see Telegram section below. |
| `telegram.bot_token` | Optional **literal** bot token in JSON. If set (non-empty), used instead of the env var named by `bot_token_env`. |
| `telegram.chat_id` | Optional **literal** chat id (string or JSON integer). If set, used instead of the env var named by `chat_id_env`. |
| `telegram.bot_token_env` | Name of env var used when `bot_token` is omitted (default `TELEGRAM_BOT_TOKEN`). Must be a valid env name. |
| `telegram.chat_id_env` | Name of env var used when `chat_id` is omitted (default `TELEGRAM_CHAT_ID`). Must be a valid env name (not the numeric id). |
| `repos[]` | Each entry: `name` (optional), `url` (SSH only), `branch`. |

### Telegram credentials

You can mix **inline** values in `config.json` and **environment** values (e.g. systemd `EnvironmentFile=…/secrets.env`):

- **Per field:** if `telegram.bot_token` is present and non-empty, it wins; otherwise the token is read from `os.environ[telegram.bot_token_env]`. Same for `telegram.chat_id` vs `chat_id_env`.

Examples:

**Env only** (recommended for production): omit `bot_token` / `chat_id`, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `secrets.env`.

**All in config** (simpler, weaker security — protect `config.json` with `chmod 600` and restrict backups):

```json
"telegram": {
  "bot_token": "123456789:AA…from BotFather…",
  "chat_id": 1380628864
}
```

**Mixed:** e.g. token in `secrets.env`, chat id in JSON as `"chat_id": "1380628864"`.

### SSH identity precedence

1. If **`GIT_SSH_COMMAND`** is already set in the process environment (for example in `secrets.env` via systemd), it is **left unchanged** and `ssh_identity_file` is ignored.
2. Otherwise, if **`ssh_identity_file`** is set in JSON, the watcher sets  
   `GIT_SSH_COMMAND='ssh -i <path> -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'`.
3. If neither applies, OpenSSH defaults for the service user apply (`~/.ssh/config`, agent, default key filenames).

You can set `GIT_SSH_COMMAND` in `secrets.env` instead of using `ssh_identity_file` in JSON.

### Telegram `secrets.env` (optional if you use inline `bot_token` / `chat_id`)

If either credential is **not** set in `config.json`, load it from the environment. Typical pattern: `/etc/git-deployer/secrets.env` (mode `600`), referenced from systemd:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

You can omit this file entirely when both `telegram.bot_token` and `telegram.chat_id` are set in JSON (not recommended for production tokens).

## Install on Ubuntu (22.04 / 24.04)

### 1. Packages

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv
```

**pip:** the `python3-pip` package installs Pip for the distro Python. Check it:

```bash
python3 -m pip --version
pip3 --version
```

If `apt` cannot install `python3-pip` (minimal image), either use **venv** (recommended for Option B below) or bootstrap Pip once:

```bash
curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
sudo python3 /tmp/get-pip.py
rm /tmp/get-pip.py
```

Then prefer `python3 -m pip …` so you always hit the interpreter you intend.

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

You need the directory that contains both **`run_watcher.py`** and the **`git_deploy_watcher/`** package on disk. Two supported ways:

#### Option A (recommended on servers): copy source — **no pip, no venv**

Works on Ubuntu/Debian **PEP 668** (“externally-managed-environment”) systems because nothing is installed into system Python.

Your checkout only needs **`run_watcher.py`** next to **`git_deploy_watcher/`** (you already have that under e.g. `/var/deploy/apps/git-deployer`). Point systemd at that path, for example:

```text
ExecStart=/usr/bin/python3 /var/deploy/apps/git-deployer/run_watcher.py --config /etc/git-deployer/config.json
```

(Optional layout used in the bundled unit: copy/sync the same tree to **`/opt/git-deploy-watcher`**.)

```bash
sudo mkdir -p /opt/git-deploy-watcher
sudo rsync -a --delete \
  --exclude '.git' \
  /path/to/git-deployer/ /opt/git-deploy-watcher/
sudo chown -R root:root /opt/git-deploy-watcher
sudo chmod -R a+rX /opt/git-deploy-watcher
sudo chmod +x /opt/git-deploy-watcher/run_watcher.py
```

The default **`git-deploy-watcher.service`** uses **`/opt/git-deploy-watcher/run_watcher.py`**. If your tree lives elsewhere (like **`/var/deploy/apps/git-deployer`**), override **`ExecStart`** accordingly.

#### Option B: virtualenv (use when you want `pip install` without touching system Python)

Modern Ubuntu blocks **`sudo pip install`** into `/usr/bin/python3` unless you pass **`--break-system-packages`** (discouraged). Prefer a **venv owned by `git-deploy`**:

```bash
sudo apt install -y python3-venv
sudo mkdir -p /var/lib/git-deploy-watcher
sudo chown git-deploy:git-deploy /var/lib/git-deploy-watcher

sudo -u git-deploy python3 -m venv /var/lib/git-deploy-watcher/venv
sudo -u git-deploy /var/lib/git-deploy-watcher/venv/bin/pip install -U pip
sudo -u git-deploy /var/lib/git-deploy-watcher/venv/bin/pip install /var/deploy/apps/git-deployer
```

Use this interpreter in systemd:

```text
ExecStart=/var/lib/git-deploy-watcher/venv/bin/python -m git_deploy_watcher --config /etc/git-deployer/config.json
```

(or `…/venv/bin/python /var/deploy/apps/git-deployer/run_watcher.py --config …` — both work once the package is installed in that venv).

#### Option C: system Python with `--break-system-packages` (last resort)

Only if you insist on no venv:

```bash
cd /path/to/git-deployer
sudo python3 -m pip install --break-system-packages .
```

Then **`/usr/bin/python3 -m git_deploy_watcher`** must be the `ExecStart` line for the same interpreter.

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

Skip this step if both **`telegram.bot_token`** and **`telegram.chat_id`** are set in `config.json` (env-only is still recommended for the token).

```bash
sudo install -m 600 /dev/null /etc/git-deployer/secrets.env
sudo nano /etc/git-deployer/secrets.env
```

Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for whichever fields you do **not** put inline in JSON. Optionally add `GIT_SSH_COMMAND`.

### 8. systemd

The bundled unit defaults to **`/opt/git-deploy-watcher/run_watcher.py`**. If your tree is under **`/var/deploy/apps/git-deployer`**, edit **`ExecStart`** before enabling:

```ini
ExecStart=/usr/bin/python3 /var/deploy/apps/git-deployer/run_watcher.py --config /etc/git-deployer/config.json
```

(With **Option B (venv)**, use **`/var/lib/git-deploy-watcher/venv/bin/python …`** instead — see section 4.)

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

## Troubleshooting

### `error: externally-managed-environment` (pip / PEP 668)

Ubuntu/Debian **do not allow** `pip install` into the system **`python3`** without **`--break-system-packages`**.

**Recommended:** use **Option A** (`run_watcher.py` + source tree) — **no pip**. Your layout `/var/deploy/apps/git-deployer` is enough if **`ExecStart`** points at that **`run_watcher.py`**.

**Or:** use **Option B** (a **venv** under `/var/lib/git-deploy-watcher/venv` and `pip install` only inside it).

Do **not** set a broken **`GIT_SSH_COMMAND`** (e.g. `ssh -i` with no key path) when running `pip`; SSH env vars are irrelevant to `pip` and a bad value can break unrelated commands.

### `No module named git_deploy_watcher`

Systemd is calling **`python3 -m git_deploy_watcher`**, but that interpreter never had the package installed (or a different `python3` is on `PATH` than the one you used with `pip`).

**Fix:** use **option A** in the install section (copy sources to `/opt/git-deploy-watcher` including `run_watcher.py` and `git_deploy_watcher/`), install the updated **`git-deploy-watcher.service`** from this repo (it uses **`run_watcher.py`**), then:

```bash
sudo systemctl daemon-reload && sudo systemctl restart git-deploy-watcher
```

Sanity check (Ctrl+C to stop once you see it looping):

```bash
sudo /usr/bin/python3 /opt/git-deploy-watcher/run_watcher.py --config /etc/git-deployer/config.json
```

## Behavior summary

- **Clone** if `{base_path}/{name}` is missing (`git clone --branch … --single-branch`).
- **Update** with `git fetch origin`, `git checkout <branch>`, `git merge --ff-only origin/<branch>` (non-fast-forward and other git failures are **logged and sent to Telegram**).
- **Dirty tree**: logs a warning; does not invent a new revision.
- **Deploy**: runs `/bin/bash start.sh` in the repo root when the current `HEAD` is not yet recorded as successfully deployed.
- **State**: after a **successful** `start.sh`, the current `HEAD` SHA is written to `state_file`. Failures keep the old entry so the next poll retries.
- **Telegram**: on **`start.sh` non-zero** and on **git errors** (clone, `status`, `rev-parse`, fetch/checkout/merge); messages are truncated to Telegram’s length limit; **rate limit** is separate per repo for **git** vs **start.sh** (at most one of each kind per 5 minutes per repo).

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python3 -m unittest discover -s tests -v
```

## License

Specify your license in a `LICENSE` file as needed.
