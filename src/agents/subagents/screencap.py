import asyncio
import tempfile
from pathlib import Path
from mcp.server import Server
from mcp.types import TextContent, Tool
import time

SCREENCAP_TOOL_NAME = "screencap"

SCREENCAP_TOOL = Tool(
    name=SCREENCAP_TOOL_NAME,
    description="Take a screenshot of the primary screen using macOS screencapture command. Returns the path to the saved screenshot file.",
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
        
        return [TextContent(type="text", text=f"Screenshot saved to: {screenshot_path}")]
    except Exception as e:
        # Clean up the temp file if it was created but capture failed
        if screenshot_path.exists():
            screenshot_path.unlink()
        raise ValueError(f"Failed to take screenshot: {str(e)}")
