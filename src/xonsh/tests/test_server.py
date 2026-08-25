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

    assert result["exit_code"] == 0
    assert result["stdout"].splitlines() == ["hello world", str(tmp_path)]
    assert result["stderr"] == ""
    assert result["timed_out"] is False


async def test_stdin_and_environment_changes() -> None:
    result = await execute_xonsh(
        "import os, sys\nprint(os.environ['MCP_XONSH_TEST'])\nprint(sys.stdin.read())",
        stdin="from stdin",
        env={"MCP_XONSH_TEST": "from env"},
    )

    assert result["exit_code"] == 0
    assert result["stdout"].splitlines() == ["from env", "from stdin"]


async def test_nonzero_exit_is_a_result() -> None:
    result = await execute_xonsh("bash -c 'echo bad >&2; exit 7'")

    assert result["exit_code"] == 7
    assert result["stdout"] == ""
    assert result["stderr"] == "bad\n"


async def test_timeout_terminates_process_group() -> None:
    result = await execute_xonsh("sleep 10", timeout_seconds=0.05)

    assert result["timed_out"] is True
    assert result["exit_code"] != 0


async def test_output_is_truncated_with_original_size() -> None:
    result = await execute_xonsh("print('abcdefghij', end='')", max_output_bytes=4)

    assert result["stdout"] == "abcd"
    assert result["stdout_truncated"] is True
    assert result["stdout_bytes"] == 10


async def test_rc_files_are_not_loaded(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config" / "xonsh"
    config_dir.mkdir(parents=True)
    (config_dir / "rc.xsh").write_text("print('RC WAS LOADED')\n")

    result = await execute_xonsh("print('clean')", env={"HOME": str(fake_home)})

    assert result["stdout"] == "clean\n"


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
