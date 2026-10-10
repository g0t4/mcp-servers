"""A lightweight MCP server exposing perception tools to subagents.

Subagents connect to this server (via langchain-mcp-adapters) to gain the
`screencap` and `locate_anything` tools directly, so a vision-capable model can
see the actual screenshot pixels rather than only a file path.
"""

import base64
import tempfile
import time
import asyncio
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from subagents.locate.worker import locate_anything_infer

mcp = FastMCP("perception")


@mcp.tool()
async def screencap(description: str = "") -> Image:
    """Take a screenshot of the primary screen (macOS `screencapture`).

    Returns the screenshot as an image so a vision-capable model can see it.
    `description` is an optional note about the screenshot's purpose.
    """
    temp_dir = Path(tempfile.gettempdir()) / "mcp_screencaps"
    temp_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time() * 1000)
    screenshot_path = temp_dir / f"screenshot_{timestamp}.png"

    process = await asyncio.create_subprocess_exec(
        "screencapture",
        "-x",
        "-m",
        str(screenshot_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"screencapture failed ({process.returncode}): {stderr.decode('utf-8')}"
        )
    return Image(path=screenshot_path)


@mcp.tool()
def locate_anything(question: str, image_path: str) -> str:
    """Answer a visual question about an image and return bounding boxes.

    Uses the LocateAnything-3B grounding model (served remotely on build21.lan).
    Returns the model's raw output, including <ref> and <box> control tokens.
    """
    return locate_anything_infer(image_path, question)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
