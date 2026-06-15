from mcp.server import Server
from mcp.types import TextContent, Tool
import asyncio

from subagents.delegate import console


COUNT_TOOL_NAME = "count"

COUNT_TOOL = Tool(
    name=COUNT_TOOL_NAME,
    description="Count to a number, for testing progress and cancellation",
    inputSchema={
        "type": "object",
        "properties": {
            "to": {"type": "integer", "description": "the integer to count to"},
        },
        "required": ["to"],
    },
)


async def count(server: Server, arguments: dict) -> list[TextContent]:
    """Count from 0 to the specified number with progress notifications."""
    count_to = arguments["to"]
    try:
        for i in range(0, count_to):
            await asyncio.sleep(0.2)

            ctx = server.request_context
            if ctx.meta and ctx.meta.progressToken:
                await ctx.session.send_progress_notification(
                    progress_token=ctx.meta.progressToken,
                    progress=(i + 1),
                    total=100,
                    message=f"Counting {i + 1} of {count_to}",
                )
            else:
                return [TextContent(type="text", text="Missing a progressToken, cannot count, please add one and try again")]
        return [TextContent(type="text", text=f"DONE counting to {count_to}")]

    except asyncio.CancelledError as error:
        console.print("tool=COUNT", error)
        raise
