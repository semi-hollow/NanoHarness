"""ripgrep 子进程的共享有界读取边界。"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path


def run_bounded_rg_lines(
    command: list[str],
    *,
    cwd: Path,
    max_lines: int,
    timeout_seconds: int = 30,
) -> tuple[list[str], bool, str]:
    """最多读取 ``max_lines + 1`` 条，命中截断后立即终止搜索进程。"""

    if max_lines <= 0 or timeout_seconds <= 0:
        raise ValueError("max_lines and timeout_seconds must be positive")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        return [], False, "ripgrep output pipes are unavailable"

    timed_out = threading.Event()

    def terminate_after_deadline() -> None:
        timed_out.set()
        process.kill()

    timer = threading.Timer(timeout_seconds, terminate_after_deadline)
    timer.daemon = True
    timer.start()
    lines: list[str] = []
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if line:
                lines.append(line)
            if len(lines) > max_lines:
                process.terminate()
                break
    finally:
        timer.cancel()

    truncated = len(lines) > max_lines
    try:
        process.wait(timeout=2 if truncated or timed_out.is_set() else None)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    stderr = process.stderr.read()
    if timed_out.is_set():
        return [], False, f"ripgrep timed out after {timeout_seconds}s"
    if not truncated and process.returncode not in {0, 1}:
        return [], False, stderr.strip() or f"rg exited with {process.returncode}"
    return lines[:max_lines], truncated, ""
