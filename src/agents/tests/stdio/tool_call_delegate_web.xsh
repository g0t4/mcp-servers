#!/usr/bin/env xonsh
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, pipe_to_jq, run_subagents

request_call_delegate = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "delegate",
            "arguments": {"description": "what is the news"},
        },
    }
)

# Wait long enough for the subagent to have a chance to run -- it can take a
# while depending on what it sets out to do! Without this sleep the script dies
# and kills `uv run` with it, silently appearing to fail when in reality the
# subagent was cranking away.
stdout = run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 0),
        (request_call_delegate, 30),
    ]
)
pipe_to_jq(stdout)
