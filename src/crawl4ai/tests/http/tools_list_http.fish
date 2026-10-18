#!/usr/bin/env fish
set request '{ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }'

# Crawl4AI 0.9.5 uses /mcp/sse or /mcp/ws for MCP sessions.
# /mcp/schema provides the same tool inventory over a simple HTTP GET.

# set -l token $CRAWL4AI_API_TOKEN
set -l token foo
if test -z "$token"
    set -l token_file "$HOME/repos/github/g0t4/mcp-servers/src/crawl4ai/.env"
    if test -r "$token_file"
        set token (string replace 'CRAWL4AI_API_TOKEN=' '' (string match 'CRAWL4AI_API_TOKEN=*' < "$token_file"))
    end
end
if test -z "$token"
    echo 'Set CRAWL4AI_API_TOKEN or create the Crawl4AI compose .env file.' >&2
    exit 1
end

curl --fail-with-body --silent --show-error \
    -H "Authorization: Bearer $token" \
    --url http://localhost:11235/mcp/schema \
    | jq

