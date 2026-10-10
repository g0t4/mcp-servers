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
            "arguments": {"description": "what time is it?"},
        },
    }
)

stdout = run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 0),
        (request_call_delegate, 2),
    ]
)
pipe_to_jq(stdout)
