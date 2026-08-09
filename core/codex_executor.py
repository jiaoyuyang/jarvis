import subprocess
import os

from core.paths import WORKSPACE_ROOT

def run_codex(prompt):
    cmd = [
        os.getenv("CODEX_BIN", "/usr/bin/codex"),
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(WORKSPACE_ROOT),
        prompt
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("CODEX_TIMEOUT", "300"))
        )

        if result.returncode != 0:
            return f"执行失败: {result.stderr}"

        return result.stdout

    except subprocess.TimeoutExpired:
        return "执行超时（Codex未在时间内返回）"
