import asyncio
import base64
import tempfile
from pathlib import Path
from mcp.server import Server
from mcp.types import ImageContent, TextContent, Tool
import time

SCREENCAP_TOOL_NAME = "screencap"

SCREENCAP_TOOL = Tool(
    name=SCREENCAP_TOOL_NAME,
    description=(
        "Take a screenshot of the primary screen using macOS screencapture command. "
        "Returns the saved screenshot path AND the image itself (as an image content "
        "block) so a vision-capable model can see the screen."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Optional description of the screenshot purpose"
            }
        },
        "required": [],
    },
)


async def screencap(server: Server, arguments: dict) -> list[TextContent]:
    """Take a screenshot of the primary screen and return the path to the file."""
    # Create a temporary directory for the screenshots
    temp_dir = Path(tempfile.gettempdir()) / "mcp_screencaps"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate a unique filename
    timestamp = int(time.time() * 1000)
    screenshot_path = temp_dir / f"screenshot_{timestamp}.png"
    
    # Use macOS screencapture command
    # -x: do not play sound
    # -m: capture the main display (primary screen)
    command = ["screencapture", "-x", "-m", str(screenshot_path)]
    
    try:
        result = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await result.communicate()
        except asyncio.CancelledError:
            # Don't leave a hung screencapture process behind when cancelled.
            result.kill()
            raise

        if result.returncode != 0:
            raise Exception(f"screencapture failed with return code {result.returncode}: {stderr.decode('utf-8')}")
        
        # Return both the path (for reference) and the actual image bytes so a
        # multimodal model in the loop can "see" the screenshot, not just read
        # the path. Base64-encode the PNG and tag it with the mime type.
        png_bytes = screenshot_path.read_bytes()
        return [
            TextContent(type="text", text=f"Screenshot saved to: {screenshot_path}"),
            ImageContent(
                type="image",
                data=base64.b64encode(png_bytes).decode("ascii"),
                mimeType="image/png",
            ),
        ]
    except Exception as e:
        # Clean up the temp file if it was created but capture failed
        if screenshot_path.exists():
            screenshot_path.unlink()
        raise ValueError(f"Failed to take screenshot: {str(e)}")
