#!/usr/bin/env xonsh

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

REQUEST_INIT = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
    '{"protocolVersion":"2024-11-05","capabilities":{},'
    '"clientInfo":{"name":"test","version":"1.0"}}}'
)

# initialized notification DOES NOT HAVE ID
NOTIFY_INITIALIZED = '{"jsonrpc":"2.0","method":"notifications/initialized"}'

REQUEST_CALL_SCREENCAP = (
    '{ "jsonrpc": "2.0", "id": 2, "method":"tools/call","params":'
    '{"name":"screencap", "arguments": {"description": "test screenshot"}}}'
)

SUBAGENTS_DIR = Path.home() / "repos/github/g0t4/mcp-servers/src/agents"


def send_message(proc, message):
    proc.stdin.write(message + "\n")
    proc.stdin.flush()


def main():
    cmd = ["uv", "run", "--directory", str(SUBAGENTS_DIR), "-m", "subagents"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Drive the MCP handshake, then the screencap tool call, over stdio.
    send_message(proc, REQUEST_INIT)
    time.sleep(2)
    send_message(proc, NOTIFY_INITIALIZED)
    send_message(proc, REQUEST_CALL_SCREENCAP)

    # Read the response stream until we find the tools/call result (id 2) which
    # carries the screenshot as an image content block.
    image_data = None
    screenshot_text = None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != 2:
            continue
        result = message.get("result", {})
        for item in result.get("content", []):
            if item.get("type") == "text" and screenshot_text is None:
                screenshot_text = item.get("text")
            if item.get("type") == "image":
                image_data = item.get("data")
        break

    # Close stdin so the stdio server shuts down cleanly.
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    if screenshot_text:
        print(screenshot_text, flush=True)

    if not image_data:
        print("No image found in screencap response", file=sys.stderr)
        sys.exit(1)

    # Pipe the decoded PNG to imgcat so it renders in the terminal.
    png_bytes = base64.b64decode(image_data)
    imgcat = subprocess.run(["imgcat"], input=png_bytes)
    if imgcat.returncode != 0:
        sys.exit(imgcat.returncode)


if __name__ == "__main__":
    main()
