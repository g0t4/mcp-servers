from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
MAX_TIMEOUT_SECONDS = 600.0
MAX_OUTPUT_BYTES = 10_000_000


def _truncate(data: bytes, limit: int) -> tuple[str, bool, int]:
    original_bytes = len(data)
    truncated = original_bytes > limit
    if truncated:
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), truncated, original_bytes


def _resolve_cwd(cwd: str | None) -> Path:
    path = Path(cwd or os.getcwd()).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"cwd does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"cwd is not a directory: {path}")
    return path


def _build_env(changes: dict[str, str | None] | None) -> dict[str, str]:
    env = os.environ.copy()
    # A headless Xonsh process must not inherit TERM=dumb: Xonsh interprets it
    # as a request to construct its readline shell even for `-c` execution.
    if not changes or "TERM" not in changes:
        env.pop("TERM", None)
    for key, value in (changes or {}).items():
        if not key or "=" in key or "\x00" in key:
            raise ValueError(f"invalid environment variable name: {key!r}")
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    # Preserve source order when Python prints and subprocess output are mixed,
    # and return the final subprocess's exact status instead of a traceback.
    env["PYTHONUNBUFFERED"] = "1"
    env["XONSH_SUBPROC_CMD_RAISE_ERROR"] = "False"
    env["XONSH_SUBPROC_RAISE_ERROR"] = "False"
    return env


async def execute_xonsh(
    code: str,
    *,
    cwd: str | None = None,
    stdin: str | None = None,
    env: dict[str, str | None] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Execute code in a fresh, non-interactive Xonsh process."""
    if not code.strip():
        raise ValueError("code must not be empty")
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"timeout_seconds must be greater than 0 and at most {MAX_TIMEOUT_SECONDS}"
        )
    if not 1 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError(
            f"max_output_bytes must be between 1 and {MAX_OUTPUT_BYTES}"
        )

    resolved_cwd = _resolve_cwd(cwd)
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "xonsh",
        "--no-rc",
        "-c",
        code,
        cwd=resolved_cwd,
        env=_build_env(env),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(None if stdin is None else stdin.encode()),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=1.0)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = await process.communicate()

    stdout_text, stdout_truncated, stdout_bytes = _truncate(
        stdout, max_output_bytes
    )
    stderr_text, stderr_truncated, stderr_bytes = _truncate(
        stderr, max_output_bytes
    )
    return {
        "exit_code": process.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "cwd": str(resolved_cwd),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "timed_out": timed_out,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
    }


def create_server() -> FastMCP:
    server = FastMCP(
        "xonsh",
        instructions=(
            "Execute commands and Python through Xonsh. Prefer concise native command "
            "syntax for simple subprocesses and ordinary Python for data processing, "
            "branching, and reusable logic. Each call is isolated and does not load rc files."
        ),
    )

    @server.tool(
        name="run_xonsh",
        description=(
            "Run Xonsh source in a fresh process. Xonsh accepts shell-like commands and "
            "ordinary Python in the same program. Returns structured stdout, stderr, exit "
            "status, timing, timeout, and truncation metadata. This tool can modify the "
            "filesystem and invoke arbitrary programs with the server's permissions."
        ),
    )
    async def run_xonsh(
        code: str,
        cwd: str | None = None,
        stdin: str | None = None,
        env: dict[str, str | None] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> str:
        result = await execute_xonsh(
            code,
            cwd=cwd,
            stdin=stdin,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        return json.dumps(result, indent=2)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCP server exposing a deterministic Xonsh execution primitive"
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    args = parser.parse_args()
    create_server().run(transport=args.transport)


if __name__ == "__main__":
    main()
