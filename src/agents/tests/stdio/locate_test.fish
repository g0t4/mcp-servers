#!/usr/bin/env fish

# Fish script to test calling the locate_anything tool

set request_init '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# initialized notification DOES NOT HAVE ID
set notify_initialized '{"jsonrpc":"2.0","method":"notifications/initialized"}'

set image_path $HOME'/repos/github/g0t4/mcp-servers/src/agents/locate/images/01-terminal.png'
if not test -f "$image_path"
    echo "Image not found"
    exit 1
end

# server side dominates so only send one request to avoid indiscriminatley long wait on client side (sleeep below)... run separate tests if wanna ask multiple questions
set request_call_locate_1 '{"jsonrpc": "2.0", "id": 2, "method":"tools/call","params":{"name":"locate_anything", "arguments": {"question": "name", "image_path": "'$image_path'"}}}'

begin
    echo $request_init
    sleep 1
    echo $notify_initialized
    sleep 1
    # LocateAnything-3B runs on CPU here and is SLOW (~44s+ for one query),
    # so keep stdin open long enough for the server to load the model AND
    # finish generating before we close the pipe (EOF kills the server early).
    echo "Waiting up to 180s for the model to load and respond (slow CPU inference)..."
    echo $request_call_locate_1
    sleep 180
end | uv run \
    --directory $HOME/repos/github/g0t4/mcp-servers/src/agents \
    -m subagents \
    | jq
