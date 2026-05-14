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
    script = repo / "start.sh"
    if not script.is_file():
        raise StartScriptError(f"missing start.sh in {repo}")
    cp = subprocess.run(
        ["/bin/bash", str(script)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if cp.returncode != 0:
        raise StartScriptError(
            f"start.sh exited with {cp.returncode}",
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
            code=cp.returncode,
        )
    return cp
