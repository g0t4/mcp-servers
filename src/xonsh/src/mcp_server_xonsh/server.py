from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import CallToolResult, Notification, Tool as MCPTool

from mcp_server_xonsh.mcp_logging import LogContext

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 131072  # my biggest code files are 64KB roughly ... so 128 is PLENTY for most agent tasks that are reasonable in one go
MAX_TIMEOUT_SECONDS = 600.0

_log = LogContext("xonsh")
console = _log.console
_verbose = False

# DO NOT FORGET THE FUCKING TYPE ANNOTATION OR YOU ARE ASS RAPED
# pydantic happily deletes shit w/o a fucking care in the world (no logging, no errors, no nothing unless the person that built the model made it strict)
RawNotification_DeleteThisAndYouGetSurpriseAssRape = Notification[dict[str, Any], str]


@dataclass
class BackgroundXonshTask:
    """Holds state for a background xonsh task."""
    task_id: str
    code: str
    process: asyncio.subprocess.Process | None = None
    done: bool = False
    result: dict | None = None
    error: str | None = None
    task: asyncio.Task | None = None
    # Streamed output buffers, drained by xonsh_status on each poll so callers
    # only see NEW output since their last status check.
    stdout_buffer: str = ""
    stderr_buffer: str = ""


_background_tasks: dict[str, BackgroundXonshTask] = {}
_server_instance: FastMCP | None = None


def _truncate_and_decode(data: bytes, limit: int) -> tuple[str, bool, int]:
    original_bytes = len(data)
    truncated = original_bytes > limit
    if truncated:
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), truncated, original_bytes


def _resolve_cwd(cwd: str | None, default_cwd: str | None = None) -> Path:
    """Resolve the working directory.

    Precedence: explicit cwd > default_cwd > server process cwd.
    """
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


async def _spawn_xonsh_process(
    code: str,
    *,
    cwd: str | None = None,
    default_cwd: str | None = None,
    stdin: str | None = None,
    env: dict[str, str | None] | None = None,
) -> asyncio.subprocess.Process:
    """Create a fresh, non-interactive Xonsh subprocess (not yet run)."""
    resolved_cwd = _resolve_cwd(cwd, default_cwd)
    return await asyncio.create_subprocess_exec(
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


async def _communicate_xonsh_process(
    process: asyncio.subprocess.Process,
    *,
    stdin: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CallToolResult:
    """Run a spawned xonsh process to completion and build the result."""
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

    stdout_text, stdout_truncated, stdout_bytes = _truncate_and_decode(stdout, max_output_bytes)
    stderr_text, stderr_truncated, stderr_bytes = _truncate_and_decode(stderr, max_output_bytes)
    is_error = process.returncode != 0 or timed_out
    structured = {
        "exit_code": process.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }
    if timed_out:
        structured["timed_out"] = True
    if stdout_truncated:
        structured["stdout_truncated"] = True
        structured["stdout_bytes"] = stdout_bytes
    if stderr_truncated:
        structured["stderr_truncated"] = True
        structured["stderr_bytes"] = stderr_bytes
    return CallToolResult(content=[], structuredContent=structured, isError=is_error)


async def _stream_xonsh_process(
    process: asyncio.subprocess.Process,
    bg: BackgroundXonshTask,
    *,
    stdin: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CallToolResult:
    """Run a spawned xonsh process, streaming output into the bg task's buffers.

    While the process runs, stdout/stderr chunks are appended to
    ``bg.stdout_buffer`` / ``bg.stderr_buffer`` so ``xonsh_status`` can drain and
    return them on each poll. When the process settles, the FULL output is used
    to build the final result (stored in ``bg.result``), regardless of how much
    has already been drained from the live buffers.
    """
    timed_out = False

    if stdin is not None and process.stdin is not None:
        process.stdin.write(stdin.encode())
        await process.stdin.drain()
        process.stdin.close()

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    async def _read_stream(
        stream: asyncio.StreamReader | None,
        chunks: list[bytes],
        buffer_attr: str,
    ) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                setattr(
                    bg,
                    buffer_attr,
                    getattr(bg, buffer_attr) + chunk.decode("utf-8", errors="replace"),
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            # Best effort: don't let a reader failure kill the background task.
            pass

    stdout_task = asyncio.create_task(
        _read_stream(process.stdout, stdout_chunks, "stdout_buffer")
    )
    stderr_task = asyncio.create_task(
        _read_stream(process.stderr, stderr_chunks, "stderr_buffer")
    )

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    # Wait for stream readers to finish draining remaining output.
    await asyncio.gather(stdout_task, stderr_task)

    stdout = b"".join(stdout_chunks)
    stderr = b"".join(stderr_chunks)

    stdout_text, stdout_truncated, stdout_bytes = _truncate_and_decode(stdout, max_output_bytes)
    stderr_text, stderr_truncated, stderr_bytes = _truncate_and_decode(stderr, max_output_bytes)
    is_error = process.returncode != 0 or timed_out
    structured = {
        "exit_code": process.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }
    if timed_out:
        structured["timed_out"] = True
    if stdout_truncated:
        structured["stdout_truncated"] = True
        structured["stdout_bytes"] = stdout_bytes
    if stderr_truncated:
        structured["stderr_truncated"] = True
        structured["stderr_bytes"] = stderr_bytes
    return CallToolResult(content=[], structuredContent=structured, isError=is_error)


async def _run_xonsh_process(
    code: str,
    *,
    cwd: str | None = None,
    default_cwd: str | None = None,
    stdin: str | None = None,
    env: dict[str, str | None] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CallToolResult:
    """Execute code in a fresh, non-interactive Xonsh process (internal)."""
    process = await _spawn_xonsh_process(
        code,
        cwd=cwd,
        default_cwd=default_cwd,
        stdin=stdin,
        env=env,
    )
    return await _communicate_xonsh_process(
        process,
        stdin=stdin,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


async def execute_xonsh(
    code: str,
    *,
    cwd: str | None = None,
    default_cwd: str | None = None,
    stdin: str | None = None,
    env: dict[str, str | None] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    background: bool = False,
    on_background_done: Callable[[dict | None, str | None, int | None], Awaitable[None]] | None = None,
) -> CallToolResult:
    """Execute code in a fresh, non-interactive Xonsh process."""
    if not code.strip():
        raise ValueError("code must not be empty")
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be greater than 0 and at most {MAX_TIMEOUT_SECONDS}")

    if background:
        task_id = uuid.uuid4().hex
        bg = BackgroundXonshTask(task_id=task_id, code=code)
        _background_tasks[task_id] = bg
        # Spawn eagerly so we can report the PID in the immediate response. The
        # agent can then act on the process directly (kill, inspect, etc.)
        # without needing a dedicated stop/status tool.
        process = await _spawn_xonsh_process(
            code,
            cwd=cwd,
            default_cwd=default_cwd,
            stdin=stdin,
            env=env,
        )
        bg.process = process
        bg.task = asyncio.create_task(
            _stream_xonsh_process(
                process,
                bg,
                stdin=stdin,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
        )

        async def _collect_result(task: asyncio.Task) -> None:
            try:
                result = await task
                bg.result = result.structuredContent or {}
            except asyncio.CancelledError:
                bg.error = "cancelled"
                raise
            except Exception as error:
                bg.error = str(error)
                console.print(f"BACKGROUND ERROR: {error}")
            finally:
                bg.done = True
                try:
                    if on_background_done is not None:
                        await on_background_done(bg.result, bg.error, process.pid if bg.process else None)
                except Exception as e:
                    console.print("on_background_done failed", e)
                    # best effort, don't break background task on callback errors

        asyncio.create_task(_collect_result(bg.task))
        return CallToolResult(
            content=[],
            structuredContent={
                "task_id": task_id,
                "background": True,
                "pid": process.pid,
            },
            isError=False,
        )

    return await _run_xonsh_process(
        code,
        cwd=cwd,
        default_cwd=default_cwd,
        stdin=stdin,
        env=env,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def _strip_titles(node: Any) -> Any:
    """Recursively drop Pydantic's auto-generated ``title`` keys from a schema.

    Pydantic adds a ``title`` to every schema node that merely restates the
    field name, e.g. "Cwd" for ``cwd``. It carries no meaning for the client, so
    we remove it to keep the tool contract terse.
    """
    if isinstance(node, dict):
        node.pop("title", None)
        for child in node.values():
            _strip_titles(child)
    elif isinstance(node, list):
        for child in node:
            _strip_titles(child)
    return node


class NoRedundantTitlesFastMCP(FastMCP):
    """FastMCP that omits Pydantic's auto-generated ``title`` keys from tool schemas.

    Pydantic emits a ``title`` for every field (and for the schema itself) that
    just echoes the field name, e.g. "Cwd" for ``cwd``. That's noise in the MCP
    contract with no extra information, so we strip it from the input schema
    before it reaches clients.
    """

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema = _strip_titles(tool.inputSchema)
        return tools


def create_server(workdir: str | None = None) -> FastMCP:
    # Pre-install a file-backed handler on the root logger so FastMCP's
    # ``configure_logging`` (which uses basicConfig and is a no-op once handlers
    # exist) keeps its framework logs on the log file instead of stderr.
    _log.configure_python_logging()

    server = NoRedundantTitlesFastMCP(
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
                     "filesystem and invoke arbitrary programs with the server's permissions. "
                     "Use background=True for long-running tasks: returns immediately with a "
                     "task_id and the process's pid (so you can act on the process directly, e.g. "
                     "kill it). Use xonsh_status to poll, and background/done notification when finished."),
    )
    async def run_xonsh(
        code: str,
        cwd: str | None = None,
        stdin: str | None = None,
        env: dict[str, str | None] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        background: bool = False,
        ctx: Context | None = None,
    ) -> CallToolResult:
        on_background_done = None
        if background and ctx is not None:
            progress_token = ctx.request_context.meta.progressToken if ctx.request_context.meta else None

            async def on_background_done(result: dict | None, error: str | None, pid: int | None = None) -> None:
                if error is not None:
                    message = f"FAILED: Background run_xonsh failed with error: {error}"
                else:
                    message = "DONE: Background run_xonsh completed"

                final_notification = RawNotification_DeleteThisAndYouGetSurpriseAssRape(
                    method="background/done",
                    params={
                        "progressToken": progress_token,
                        "message": message,
                        # "related_request_id": str(ctx.request_id), # FYI I don't need this b/c I already have it in progressToken in my case => only add back if needed for some specific client issue
                        "result": result,
                        "error": error,
                        "pid": pid,
                    },
                )

                # * avoid vim.NIL (due to null/None values)
                if final_notification.params["result"] is None:
                    del final_notification.params["result"]
                if final_notification.params.get("error") is None:
                    del final_notification.params["error"]
                if final_notification.params.get("pid") is None:
                    del final_notification.params["pid"]

                if progress_token is None:
                    # client side might struggle to correlate, but I bet agents will figure it out
                    console.print("missing progress_token, responding w/ background/done anyways")

                await ctx.request_context.session.send_notification(final_notification)

        return await execute_xonsh(
            code,
            cwd=cwd,
            default_cwd=workdir,
            stdin=stdin,
            env=env,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            background=background,
            on_background_done=on_background_done,
        )

    @server.tool(
        name="xonsh_status",
        description=("Check the status of a background xonsh task started with "
                     "run_xonsh (background=True). Reports done (true/false) plus "
                     "the task's result or error. While running, returns any "
                     "newly accumulated stdout/stderr since the last poll (buffers "
                     "are drained, so output is not repeated). Also includes the "
                     "process pid."),
    )
    async def xonsh_status(task_id: str) -> CallToolResult:
        bg = _background_tasks.get(task_id)
        if bg is None:
            return CallToolResult(
                content=[],
                structuredContent={"error": f"Unknown task_id: {task_id}"},
                isError=True,
            )
        if not bg.done:
            structured: dict[str, Any] = {
                "task_id": task_id,
                "done": False,
                "pid": bg.process.pid if bg.process else None,
            }
            # Surface any new output accumulated since the last poll, then
            # DRAIN the buffers so subsequent polls only see NEW output.
            if bg.stdout_buffer:
                structured["stdout"] = bg.stdout_buffer
                bg.stdout_buffer = ""
            if bg.stderr_buffer:
                structured["stderr"] = bg.stderr_buffer
                bg.stderr_buffer = ""
            return CallToolResult(
                content=[],
                structuredContent=structured,
                isError=False,
            )
        if bg.error:
            return CallToolResult(
                content=[],
                structuredContent={
                    "task_id": task_id,
                    "done": True,
                    "error": bg.error,
                    "pid": bg.process.pid if bg.process else None,
                },
                isError=True,
            )
        return CallToolResult(
            content=[],
            structuredContent={
                "task_id": task_id,
                "done": True,
                "result": bg.result or {},
                "pid": bg.process.pid if bg.process else None,
            },
            isError=False,
        )

    global _server_instance
    _server_instance = server

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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging for debugging.",
    )
    args = parser.parse_args()
    global _verbose
    _verbose = args.verbose
    create_server(workdir=args.workdir).run(transport=args.transport)


if __name__ == "__main__":
    main()
