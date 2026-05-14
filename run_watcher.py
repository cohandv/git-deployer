#!/usr/bin/env python3
"""Run the watcher without ``pip install`` (adds this file's directory to ``sys.path``).

Install by copying the repository to e.g. ``/opt/git-deploy-watcher`` so you have::

    /opt/git-deploy-watcher/run_watcher.py
    /opt/git-deploy-watcher/git_deploy_watcher/

Then point systemd at ``/usr/bin/python3 /opt/git-deploy-watcher/run_watcher.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from git_deploy_watcher.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
