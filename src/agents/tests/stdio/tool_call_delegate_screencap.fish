#!/usr/bin/env fish

set request_init '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# initialized notification DOES NOT HAVE ID
set notify_initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# Ask the subagent to take a screencap and tell us what it sees.
# The subagent has `run_process` (and fetch), so it can run `screencapture`.
set request_call_delegate '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"delegate","arguments":{"description": "Take a screenshot of the current screen and tell me what you see. Use run_process to run: screencapture -x -m /tmp/subagent_screencap.png Then describe the content of the screenshot (what is on screen). Report the path and your description."}}}'

begin
    echo $request_init
    sleep 1
    echo $notify_initialized
    echo $request_call_delegate
    sleep 45  # subagents can take a while (multiple tool calls + model generation)
end | uv run \
    --directory ~/repos/github/g0t4/mcp-servers/src/agents \
    -m subagents \
    | jq
