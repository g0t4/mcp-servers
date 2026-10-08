#!/usr/bin/env fish

set request_init '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# initialized notification DOES NOT HAVE ID
set notify_initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# run_xonsh takes `code` (xonsh source). Date is a good smoke test.
set request_call_run_xonsh '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"run_xonsh", "arguments": { "code": "import datetime\nprint(datetime.date.today().isoformat())" }}}'

begin
    echo $request_init
    sleep 1
    echo $notify_initialized
    echo $request_call_run_xonsh
    sleep 3  # wait for xonsh subprocess to finish
end | uv run \
    --directory ~/repos/github/g0t4/mcp-servers/src/xonsh \
    mcp-server-xonsh \
    | jq
