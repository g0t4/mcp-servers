#!python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, run_subagents

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
                    "Please run these tool calls serially (one at a time, not in parallel) and each in a separate tool call request:"
                    "  `sleep 0.25`\n"
                    "  `sleep 0.5`\n"
                    "  `sleep 0.75`\n"
                    "\n"
                    "Do not combine these into one command line call, nor into parallel tool call requests."
                ),
                "background": True,
            },
        },
    }
)

run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 0),
        (request_call_delegate, 10),
    ]
)
