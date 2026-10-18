#!python3
import json
import sys
from pathlib import Path

from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, test_messages

request_list_tools = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
    }
)

test_messages(
    [
        # # ifd you don't send init first, listing tools will fail
        # (REQUEST_INIT, 1),
        # (NOTIFY_INITIALIZED, 1),
        (request_list_tools, 1),
    ]
)
