from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server_xonsh.server import create_server, execute_xonsh


async def test_combines_xonsh_commands_and_python(tmp_path: Path) -> None:
    result = await execute_xonsh(
        "name = 'world'\nprint(f'hello {name}')\npwd",
        cwd=str(tmp_path),
    )

    assert result.isError is False
    s = result.structuredContent
    assert s is not None
    assert s["exit_code"] == 0
    assert s["stdout"].splitlines() == ["hello world", str(tmp_path)]
    assert s["stderr"] == ""
    assert "timed_out" not in s
    assert "cwd" not in s
    assert "duration_ms" not in s


async def test_stdin_and_environment_changes() -> None:
    result = await execute_xonsh(
        "import os, sys\nprint(os.environ['MCP_XONSH_TEST'])\nprint(sys.stdin.read())",
        stdin="from stdin",
        env={"MCP_XONSH_TEST": "from env"},
    )

    assert result.isError is False
    s = result.structuredContent
    assert s is not None
    assert s["exit_code"] == 0
    assert s["stdout"].splitlines() == ["from env", "from stdin"]
    assert "cwd" not in s


async def test_nonzero_exit_is_a_result() -> None:
    result = await execute_xonsh("bash -c 'echo bad >&2; exit 7'")

    assert result.isError is True
    s = result.structuredContent
    assert s is not None
    assert s["exit_code"] == 7
    assert s["stdout"] == ""
    assert s["stderr"] == "bad\n"
    assert "cwd" not in s


async def test_timeout_terminates_process_group() -> None:
    result = await execute_xonsh("sleep 10", timeout_seconds=0.05)

    assert result.isError is True
    s = result.structuredContent
    assert s is not None
    assert s["timed_out"] is True
    assert s["exit_code"] != 0


async def test_output_is_truncated_with_original_size() -> None:
    result = await execute_xonsh("print('abcdefghij', end='')", max_output_bytes=4)

    assert result.isError is False
    s = result.structuredContent
    assert s is not None
    assert s["stdout"] == "abcd"
    assert s["stdout_truncated"] is True
    assert s["stdout_bytes"] == 10
    assert "stderr_bytes" not in s


async def test_rc_files_are_not_loaded(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config" / "xonsh"
    config_dir.mkdir(parents=True)
    (config_dir / "rc.xsh").write_text("print('RC WAS LOADED')\n")

    result = await execute_xonsh("print('clean')", env={"HOME": str(fake_home)})

    assert result.isError is False
    s = result.structuredContent
    assert s is not None
    assert s["stdout"] == "clean\n"
    assert "cwd" not in s


async def test_validates_arguments() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        await execute_xonsh("  ")
    with pytest.raises(ValueError, match="cwd does not exist"):
        await execute_xonsh("pwd", cwd="/definitely/not/here")
    with pytest.raises(ValueError, match="timeout_seconds"):
        await execute_xonsh("pwd", timeout_seconds=0)


def test_server_exposes_run_xonsh() -> None:
    server = create_server()
    tools = server._tool_manager.list_tools()

    tool = next(tool for tool in tools if tool.name == "run_xonsh")
    schema = tool.parameters
    assert schema["required"] == ["code"]
    assert json.dumps(schema)


async def test_background_execution_returns_task_id_immediately() -> None:
    result = await execute_xonsh("print('background test')", background=True)

    assert result.isError is False
    s = result.structuredContent
    assert s is not None
    assert s["background"] is True
    assert "task_id" in s
    assert isinstance(s["pid"], int)
    assert s["pid"] > 0


async def test_background_task_completes_and_stores_result() -> None:
    result = await execute_xonsh("print('background done')", background=True)
    s = result.structuredContent
    assert s is not None
    task_id = s["task_id"]

    # Poll until done
    from mcp_server_xonsh.server import _background_tasks
    import asyncio

    for _ in range(20):
        bg = _background_tasks.get(task_id)
        if bg and bg.done:
            break
        await asyncio.sleep(0.1)

    assert bg is not None
    assert bg.done is True
    assert bg.error is None
    assert bg.result is not None
    assert bg.result["exit_code"] == 0
    assert "background done" in bg.result["stdout"]
    assert bg.process is not None
    assert bg.process.pid == s["pid"]


async def test_xonsh_status_streams_and_drains_stdout_while_running() -> None:
    """xonsh_status returns newly accumulated stdout and drains it between polls."""
    from mcp_server_xonsh.server import _background_tasks
    import asyncio

    server = create_server()

    # Background task that prints, then sleeps, then prints again.
    result = await execute_xonsh(
        "import time\nprint('first')\ntime.sleep(0.5)\nprint('second')",
        background=True,
    )
    s = result.structuredContent
    assert s is not None
    task_id = s["task_id"]

    bg = _background_tasks.get(task_id)
    assert bg is not None

    # Wait until 'first' has been streamed into the buffer.
    for _ in range(20):
        if "first" in bg.stdout_buffer:
            break
        await asyncio.sleep(0.05)

    assert "first" in bg.stdout_buffer
    assert "second" not in bg.stdout_buffer

    # First poll via the xonsh_status tool: returns accumulated stdout, then drains.
    status_result = await server.call_tool("xonsh_status", {"task_id": task_id})
    status_content = status_result.structuredContent or {}
    assert status_content.get("done") is False
    assert "first" in status_content.get("stdout", "")
    assert "second" not in status_content.get("stdout", "")

    # Buffer is drained after the poll.
    assert bg.stdout_buffer == ""

    # Second poll before 'second' arrives: no stdout since drained.
    status_result_2 = await server.call_tool("xonsh_status", {"task_id": task_id})
    status_content_2 = status_result_2.structuredContent or {}
    assert status_content_2.get("done") is False
    assert "stdout" not in status_content_2 or status_content_2["stdout"] == ""

    # Wait for the task to finish; the full result is stored in bg.result.
    for _ in range(30):
        if bg.done:
            break
        await asyncio.sleep(0.05)

    assert bg.done is True
    assert bg.result is not None
    assert "first" in bg.result["stdout"]
    assert "second" in bg.result["stdout"]


def test_server_exposes_xonsh_status() -> None:
    server = create_server()
    tools = server._tool_manager.list_tools()
    tool_names = [tool.name for tool in tools]
    assert "xonsh_status" in tool_names
