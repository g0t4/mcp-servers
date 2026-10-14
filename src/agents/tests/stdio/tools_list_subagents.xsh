#!/usr/bin/env xonsh
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, run_subagents

request_list_tools = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }
)

run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 1),
        (request_list_tools, 3),
    ]
)
