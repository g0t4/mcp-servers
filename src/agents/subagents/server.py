import rich
import asyncio
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

DEBUGGING = False

async def serve() -> None:
    server = Server("subagents")
    await setup_agent()  # PRN await this after server running?

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [DELEGATE_TOOL]
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

            if requested_tool != DELEGATE_TOOL_NAME:
                raise McpError(ErrorData(code=1, message=f"You made up a tool... you asked for {requested_tool}...", data={"valid_tools": DELEGATE_TOOL}))

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

    # * start the server
    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=False)
