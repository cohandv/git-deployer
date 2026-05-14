from __future__ import annotations

import subprocess
from pathlib import Path


class StartScriptError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stdout: str = "",
        stderr: str = "",
        code: int | None = None,
    ):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.code = code


def run_start_sh(repo: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run ``start.sh`` with its **current working directory** set to the repo root (clone dir)."""
    root = repo.resolve()
    script = root / "start.sh"
    if not script.is_file():
        raise StartScriptError(f"missing start.sh in {root}")
    run_env = dict(env)
    root_s = str(root)
    run_env["PWD"] = root_s
    run_env["GIT_DEPLOY_REPO_ROOT"] = root_s
    # Relative script name + cwd=root guarantees the process runs inside the cloned tree.
    try:
        cp = subprocess.run(
            ["/bin/bash", "start.sh"],
            cwd=root_s,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        so = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
        se = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else "")
        raise StartScriptError(
            f"start.sh timed out after {timeout}s",
            stdout=so or "",
            stderr=se or "",
            code=None,
        ) from e
    if cp.returncode != 0:
        raise StartScriptError(
            f"start.sh exited with {cp.returncode}",
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
            code=cp.returncode,
        )
    return cp
