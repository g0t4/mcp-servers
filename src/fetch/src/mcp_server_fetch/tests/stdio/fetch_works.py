#!python3
import json
import sys
from pathlib import Path

from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, test_messages

fetch = json.dumps({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "fetch",
        "arguments": {
            "url": "https://manpages.org/ls"
        }
    },
}, )

test_messages([
    (REQUEST_INIT, 1),
    (NOTIFY_INITIALIZED, 1),
    (fetch, 3),
])
