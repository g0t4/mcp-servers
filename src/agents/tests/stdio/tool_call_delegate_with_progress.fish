#!/usr/bin/env fish

set request_init '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# initialized notification DOES NOT HAVE ID
set notify_initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'

set request_call_delegate '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"delegate", "_meta": { "progressToken": "req-42-progress" } , "arguments":{"description": "run sleep ten times serially (not in parallel) where each time you add 0.25 more seconds... so sleep 0.25 to start then sleep 0.5 then sleep 0.75 etc - run each as a separate tool call, not one giant command"}}}'

begin
    echo $request_init
    sleep 1
    echo $notify_initialized
    echo $request_call_delegate
    sleep 10 
end | uv run \
    --directory ~/repos/github/g0t4/mcp-servers/src/agents \
    -m subagents \
    | jq
