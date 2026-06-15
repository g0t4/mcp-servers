#!/usr/bin/env fish

set request_init '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# initialized notification DOES NOT HAVE ID
set notify_initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'

set request_call_delegate '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"delegate","arguments":{"description":"what time is it?"}}}'

begin
    echo $request_init
    sleep 1
    echo $notify_initialized
    echo $request_call_delegate
    sleep 2
end | uv run \
    --directory ~/repos/github/g0t4/mcp-servers/src/agents \
    -m subagents \
    | jq
