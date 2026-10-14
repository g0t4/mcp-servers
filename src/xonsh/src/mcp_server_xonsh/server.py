from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from mcp_server_xonsh.mcp_logging import LogContext

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 131072  # my biggest code files are 64KB roughly ... so 128 is PLENTY for most agent tasks that are reasonable in one go
MAX_TIMEOUT_SECONDS = 600.0

_log = LogContext("xonsh")
console = _log.console


def _truncate(data: bytes, limit: int) -> tuple[str, bool, int]:
    original_bytes = len(data)
    truncated = original_bytes > limit
    if truncated:
        # TODO? don't we need decode here too? perhaps decode first then limit after?
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), truncated, original_bytes


def _resolve_cwd(cwd: str | None, default_cwd: str | None = None) -> Path:
    path = Path(cwd or default_cwd or os.getcwd()).expanduser().resolve()
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
    env["COLOR_RESULTS"] = "False" # https://xon.sh/envvars.html#COLOR_RESULTS
    env["XONSH_SUBPROC_CMD_RAISE_ERROR"] = "False"
    env["XONSH_SUBPROC_RAISE_ERROR"] = "False"
    return env


def _result_to_content(result: dict[str, Any]) -> list[TextContent]:
    """Map an execution result to labeled text content blocks.

    Each block carries its field name (STDOUT, STDERR, EXIT_CODE, ...) so the
    result stays readable to models and matches the MCP text content shape the
    client formatters expect. Emitting blocks instead of a monolithic JSON blob
    also avoids duplicating the result into `structuredContent`.
    """
    blocks: list[TextContent] = []
    if result["stdout"]:
        blocks.append(TextContent(type="text", text=result["stdout"], name="STDOUT"))
    if result["stderr"]:
        blocks.append(TextContent(type="text", text=result["stderr"], name="STDERR"))
    blocks.append(TextContent(
        type="text",
        name="EXIT_CODE",
        text=str(result["exit_code"]),
    ))
    metadata = [
        f"CWD: {result['cwd']}",
        f"DURATION_MS: {result['duration_ms']}",
        #
        # I don't think we need these... not sure though...
        #  I suppose the client might decide not to give it back to the model if so... but then I'd really want that to be its own line item instead of need to parse METADATA for this
        # f"STDOUT BYTES: {result['stdout_bytes']}",
        # f"STDERR BYTES: {result['stderr_bytes']}",
    ]
    if result['timed_out'] == True:
        metadata.append(f"TIMED OUT: {result['timed_out']}")
    if result['stdout_truncated'] == True:
        metadata.append(f"STDOUT TRUNCATED: {result['stdout_truncated']}")
    if result['stderr_truncated'] == True:
        metadata.append(f"STDERR TRUNCATED: {result['stderr_truncated']}")
    blocks.append(TextContent(
        type="text",
        name="METADATA",
        text="\n".join(metadata),
    ))
    return blocks


async def execute_xonsh(
    code: str,
    *,
    cwd: str | None = None,
    default_cwd: str | None = None,
    stdin: str | None = None,
    env: dict[str, str | None] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Execute code in a fresh, non-interactive Xonsh process."""
    if not code.strip():
        raise ValueError("code must not be empty")
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be greater than 0 and at most {MAX_TIMEOUT_SECONDS}")

    resolved_cwd = _resolve_cwd(cwd, default_cwd)
    code_preview = code if len(code) <= 200 else code[:200] + "..."
    console.print(f"[bold green]run_xonsh[/] [cyan]{resolved_cwd}[/] "
                  f"[dim]timeout={timeout_seconds}s max_out={max_output_bytes}[/]")
    console.print(f"  [dim]{code_preview}[/]")
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

    stdout_text, stdout_truncated, stdout_bytes = _truncate(stdout, max_output_bytes)
    stderr_text, stderr_truncated, stderr_bytes = _truncate(stderr, max_output_bytes)
    duration_ms = round((time.monotonic() - started) * 1000)
    console.print(f"  [dim]exit[/] [cyan]{process.returncode}[/] "
                  f"[dim]duration_ms={duration_ms}[/] "
                  f"[dim]timed_out={timed_out}[/] "
                  f"[dim]stdout_truncated={stdout_truncated}[/] "
                  f"[dim]stderr_truncated={stderr_truncated}[/] "
                  # f"[dim]stdout_bytes={stdout_bytes}[/] "
                  # f"[dim]stderr_bytes={stderr_bytes}[/]"
                  )
    return {
        "exit_code": process.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "cwd": str(resolved_cwd),
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
    }


def create_server(workdir: str | None = None) -> FastMCP:
    # Pre-install a file-backed handler on the root logger so FastMCP's
    # ``configure_logging`` (which uses basicConfig and is a no-op once handlers
    # exist) keeps its framework logs on the log file instead of stderr.
    _log.configure_python_logging()

    server = FastMCP(
        "xonsh",
        instructions=("Execute commands and Python through Xonsh. Prefer concise native command "
                      "syntax for simple subprocesses and ordinary Python for data processing, "
                      "branching, and reusable logic. Each call is isolated and does not load rc files."),
    )

    @server.tool(
        name="run_xonsh",
        description=("Run Xonsh source in a fresh process. Xonsh accepts shell-like commands and "
                     "ordinary Python in the same program. Returns structured stdout, stderr, exit "
                     "status, timing, timeout, and truncation metadata. This tool can modify the "
                     "filesystem and invoke arbitrary programs with the server's permissions."),
    )
    async def run_xonsh(
        code: str,
        cwd: str | None = None,
        stdin: str | None = None,
        env: dict[str, str | None] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> CallToolResult:
        result = await execute_xonsh(
            code,
            cwd=cwd,
            default_cwd=workdir,
            stdin=stdin,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        return CallToolResult(
            content=_result_to_content(result),
            isError=False,
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server exposing a deterministic Xonsh execution primitive")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument(
        "--workdir",
        default=os.environ.get("MCP_XONSH_WORKDIR"),
        help=("Default working directory for run_xonsh when no cwd is provided. "
              "Falls back to the MCP_XONSH_WORKDIR environment variable."),
    )
    args = parser.parse_args()
    create_server(workdir=args.workdir).run(transport=args.transport)


if __name__ == "__main__":
    main()
