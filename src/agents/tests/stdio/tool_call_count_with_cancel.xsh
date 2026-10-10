#!/usr/bin/env xonsh
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, pipe_to_jq, run_subagents

request_call_count = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "count",
            "arguments": {"to": 10},
            "_meta": {"progressToken": "my-request2"},
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
        (NOTIFY_INITIALIZED, 0.2),
        # Enough time to get 5 progress notifications before cancelling.
        (request_call_count, 1),
        # Just long enough to receive the server's cancelled error response.
        (notify_cancel, 0.2),
    ]
)
pipe_to_jq(stdout)
