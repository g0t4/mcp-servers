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

async def serve() -> None:
    server = Server("subagents")
    await setup_agent()  # PRN await this after server running?

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [DELEGATE_TOOL, COUNT_TOOL]

        # @server.list_prompts()
        # async def list_prompts() -> list[Prompt]:
        #     return [Prompt( arguments=[PromptArgument(description="", agent_type="", required=True)],)]

    async def count(count_to: int):
        try:
            # for testing cancellation timing and progress notifications
            for i in range(0, count_to):
                await asyncio.sleep(0.2)

                ctx = server.request_context
                if ctx.meta and ctx.meta.progressToken:
                    await ctx.session.send_progress_notification(
                        progress_token=ctx.meta.progressToken,
                        progress=(i + 1),
                        total=100,
                    )
                else:
                    return [TextContent(type="text", text=f"Missing a progressToken, cannot count, please add one and try again")]
            return [TextContent(type="text", text=f"DONE counting to {count_to}")]

        except asyncio.CancelledError as error:
            # TODO use logging instead of console.print w/ rich... can still use rich to print to file... as sink to the console object?
            console.print("tool=COUNT", error)
            # FYI cannot send a progress notification... instead, server sends cancel confirm and that's it for comms
            raise

    @server.call_tool()
    async def call_tool(requested_tool, arguments: dict) -> list[TextContent]:
        try:
            if requested_tool == COUNT_TOOL_NAME:
                # PRN remove unregistered count tool, purely for testing cancel and progress notifications
                to = int(arguments['to'])
                return await count(to)
            elif requested_tool != DELEGATE_TOOL_NAME:
                raise McpError(ErrorData(code=1, message=f"You made up a tool... you asked for {requested_tool}...", data={"valid_tools": DELEGATE_TOOL}))

            description = arguments.get("description")
            agent_type = arguments.get("agent_type", "general")
            return await delegate_tool(description, agent_type)

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
