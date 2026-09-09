import rich
import asyncio
import os
from pathlib import Path
from mcp.shared.exceptions import McpError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    ErrorData,
    GetPromptResult,
    Prompt,
    PromptArgument,
    TextContent,
    Tool,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from subagents.delegate import *
from subagents.count import COUNT_TOOL, COUNT_TOOL_NAME, count
from subagents.screencap import SCREENCAP_TOOL, SCREENCAP_TOOL_NAME, screencap
from subagents.locate.tool import LOCATE_ANYTHING_TOOL, LOCATE_ANYTHING_TOOL_NAME, locate_anything

DEBUGGING = False


def _resolve_workdir(workdir: str | None = None) -> str | None:
    """Resolve a workdir CLI arg, validating it exists and is a directory."""
    if workdir is None:
        return None
    path = Path(workdir).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"workdir does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"workdir is not a directory: {path}")
    return str(path)


def create_server() -> Server:
    """Create a configured subagents MCP server.

    Returns:
        A configured Server instance with tools and prompts.
    """
    server = Server("subagents")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [DELEGATE_TOOL, SCREENCAP_TOOL, LOCATE_ANYTHING_TOOL]
        if DEBUGGING:
            tools.append(COUNT_TOOL)
        return tools

        # @server.list_prompts()
        # async def list_prompts() -> list[Prompt]:
        #     return [Prompt( arguments=[PromptArgument(description="", agent_type="", required=True)],)]

    @server.call_tool()
    async def call_tool(requested_tool, arguments: dict) -> list[TextContent]:
        try:
            if requested_tool == COUNT_TOOL_NAME:
                # PRN remove unregistered count tool, purely for testing cancel and progress notifications
                return await count(server, arguments)

            if requested_tool == SCREENCAP_TOOL_NAME:
                return await screencap(server, arguments)

            if requested_tool == LOCATE_ANYTHING_TOOL_NAME:
                # Inference is blocking CPU/GPU work; run it in a worker thread so
                # the event loop stays free to process cancellations and other
                # requests while the model generates.
                result = await asyncio.to_thread(locate_anything, **arguments)
                return [TextContent(type="text", text=result)]

            if requested_tool != DELEGATE_TOOL_NAME:
                valid_tools = [DELEGATE_TOOL.name, SCREENCAP_TOOL_NAME, LOCATE_ANYTHING_TOOL_NAME]
                if DEBUGGING:
                    valid_tools.append(COUNT_TOOL_NAME)
                raise McpError(ErrorData(code=1, message=f"You made up a tool... you asked for {requested_tool}...", data={"valid_tools": valid_tools}))

            # Extract context for progress notifications
            ctx = server.request_context
            progress_token = ctx.meta.progressToken if ctx.meta else None

            # Build the on_tool_start callback that sends MCP progress notifications
            async def on_tool_start(tool_name: str, tool_args: dict, tool_start_count: int) -> None:
                if progress_token is not None:
                    tool_args_str = str(tool_args)
                    message = f"Running tool: {tool_name} args={tool_args_str}"
                    await ctx.session.send_progress_notification(
                        progress_token=progress_token,
                        progress=tool_start_count,
                        message=message,
                    )

            description = arguments.get("description")
            agent_type = arguments.get("agent_type", "general")
            recursion_limit = arguments.get("recursion_limit", DEFAULT_RECURSION_LIMIT)
            return await delegate_tool(description, agent_type, recursion_limit, on_tool_start=on_tool_start)

        except asyncio.CancelledError:
            # TODO log unhandled cancellation? so I know that I need to push it inside the inner tool function?
            raise
        # FYI put unhandled exceptions here (outside of tool logic)
        except ValueError as error:
            rich.inspect(error, console=console)
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(error)))
        except Exception as error:
            rich.inspect(error, console=console)
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(error)))

    return server


async def serve(workdir: str | None = None) -> None:
    resolved_workdir = _resolve_workdir(workdir)
    if resolved_workdir is not None:
        os.chdir(resolved_workdir)
    server = create_server()
    await setup_agent()  # PRN await this after server running?

    # * start the server
    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=False)
