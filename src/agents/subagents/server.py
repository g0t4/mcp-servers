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
from subagents.delegate import DELEGATE_DEFINITION, DELEGATE_TOOL, delegate_tool, setup_agent, console

async def serve() -> None:
    server = Server("subagents")
    await setup_agent()  # PRN await this after server running?

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [DELEGATE_DEFINITION]

        # @server.list_prompts()
        # async def list_prompts() -> list[Prompt]:
        #     return [Prompt( arguments=[PromptArgument(description="", agent_type="", required=True)],)]

    @server.call_tool()
    async def call_tool(requested_tool, arguments: dict) -> list[TextContent]:
        if requested_tool != DELEGATE_TOOL:
            raise McpError(ErrorData(code=1, message=f"You made up a tool... you asked for {requested_tool}...", data={"valid_tools": DELEGATE_TOOL}))

        try:
            description = arguments.get("description")
            agent_type = arguments.get("agent_type", "general")
            return await delegate_tool(description, agent_type)

        # FYI put unhandled exceptions here (outside of tool logic)
        except ValueError as e:
            console.print("ValueErorr", str(e))
            raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
        except Exception as e:
            console.print(str(e))
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))

    # * start the server
    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=False)
