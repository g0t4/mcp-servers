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
            "arguments": {"description": "what time is it?"},
        },
    }
)

notify_cancel = json.dumps(
    {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": 2, "reason": "test cancel"},
    }
)

stdout = run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 0),
        # Cancel before we can get a response, else the cancel is ignored.
        (request_call_delegate, 0.5),
        (notify_cancel, 0.5),
    ]
)
pipe_to_jq(stdout)
