# MCP Xonsh

One agent primitive that speaks both subprocess and Python:

```xonsh
git status --short
```

```xonsh
from pathlib import Path

for path in Path(".").rglob("*.py"):
    if path.stat().st_size > 1_000_000:
        print(path)
```

`run_xonsh` executes each request in a fresh `xonsh --no-rc` process. It does
not load personal Xonsh configuration or retain state between calls.

## Run

```console
uv run mcp-server-xonsh
```

Example MCP configuration:

```json
{
  "mcpServers": {
    "xonsh": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/Users/wesdemos/repos/github/g0t4/mcp-servers/src/xonsh",
        "mcp-server-xonsh"
      ]
    }
  }
}
```

## Tool

`run_xonsh` accepts:

- `code` (required): Xonsh source code.
- `cwd`: working directory; defaults to the server's current directory.
- `stdin`: text delivered to standard input.
- `env`: environment additions; a `null` value removes a variable.
- `timeout_seconds`: `30` by default, up to `600`.
- `max_output_bytes`: retained separately for stdout and stderr; defaults to
  1 MB and allows up to 10 MB. The process is still fully drained to avoid
  deadlocks, and the result reports original byte counts and truncation.

The JSON result includes `exit_code`, separate `stdout` and `stderr`, resolved
`cwd`, elapsed milliseconds, timeout state, and output-size metadata. A nonzero
exit is returned normally so the agent can inspect and respond to the failure.

This is arbitrary code execution with the MCP server process's permissions. It
is an execution boundary, not a sandbox.

## Develop

```console
uv sync
uv run pytest
uv run ruff check .
```
