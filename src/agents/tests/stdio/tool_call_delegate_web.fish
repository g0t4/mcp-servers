#!/usr/bin/env fish

set request_init '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# initialized notification DOES NOT HAVE ID
set notify_initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# set request_call_delegate '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"delegate","arguments":{"description": "lookup weather for 98142"}}}'
set request_call_delegate '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"delegate","arguments":{"description": "what is the news"}}}'

begin
    echo $request_init
    sleep 1
    echo $notify_initialized
    echo $request_call_delegate
    sleep 30  # wait enough time for subagent to sufficiently have a chance to run! it can take a while depending on what it sets out to do! 
    # just ctrl-c to stop
    # w/o sleep this shell script dies and kills `uv run` with it... silently appears to fail.. when in reality the subagent was cranking away!
end | uv run \
    --directory ~/repos/github/g0t4/mcp-servers/src/agents \
    -m subagents \
    | jq
