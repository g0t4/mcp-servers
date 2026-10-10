#!/usr/bin/env xonsh
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, pipe_to_jq, run_subagents

# Ask the subagent to take a screencap and tell us what it sees. The subagent
# has `run_process` (and fetch), so it can run `screencapture`.
request_call_delegate = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "delegate",
            "arguments": {
                "description": (
                    "Take a screenshot of the current screen and tell me what you see. "
                    "Use run_process to run: screencapture -x -m /tmp/subagent_screencap.png "
                    "Then describe the content of the screenshot (what is on screen). "
                    "Report the path and your description."
                )
            },
        },
    }
)

# Subagents can take a while (multiple tool calls + model generation), so keep
# stdin open long enough for the subagent to finish before EOF kills the pipe.
stdout = run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 0),
        (request_call_delegate, 45),
    ]
)
pipe_to_jq(stdout)
