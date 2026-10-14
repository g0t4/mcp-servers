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
    assert "cwd" in s


async def test_nonzero_exit_is_a_result() -> None:
    result = await execute_xonsh("bash -c 'echo bad >&2; exit 7'")

    assert result.isError is True
    s = result.structuredContent
    assert s is not None
    assert s["exit_code"] == 7
    assert s["stdout"] == ""
    assert s["stderr"] == "bad\n"
    assert "cwd" in s


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
    assert "cwd" in s


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
