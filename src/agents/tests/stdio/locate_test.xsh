#!/usr/bin/env xonsh
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_stdio import NOTIFY_INITIALIZED, REQUEST_INIT, pipe_to_jq, run_subagents

IMAGE_PATH = (
    Path.home()
    / "repos/github/g0t4/mcp-servers/src/agents/locate/images/01-terminal.png"
)

if not IMAGE_PATH.is_file():
    print("Image not found", file=sys.stderr)
    sys.exit(1)

# The server side dominates, so only send one request to avoid an
# indiscriminately long wait on the client side; run separate tests to ask
# multiple questions. LocateAnything-3B is served remotely on build21.lan
# (llama-server, GPU), so responses are fast (<1s). Keep stdin open a moment
# longer than the inference to be safe -- EOF closes the pipe and kills the
# server early.
print("Waiting for the model to respond...", file=sys.stderr)

request_call_locate_1 = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "locate_anything",
            "arguments": {"question": "name", "image_path": str(IMAGE_PATH)},
        },
    }
)

stdout = run_subagents(
    [
        (REQUEST_INIT, 1),
        (NOTIFY_INITIALIZED, 1),
        (request_call_locate_1, 5),
    ]
)
pipe_to_jq(stdout)
