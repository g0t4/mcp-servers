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
            "_meta": {"progressToken": "req-42-progress"},
            "arguments": {
                "description": (
                    "run sleep ten times serially (not in parallel) where each "
                    "time you add 0.25 more seconds... so sleep 0.25 to start "
                    "then sleep 0.5 then sleep 0.75 etc - run each as a separate "
                    "tool call, not one giant command"
                )
            },
        },
    }
)

stdout = run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 0),
        (request_call_delegate, 10),
    ]
)
pipe_to_jq(stdout)
