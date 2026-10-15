import rich
import asyncio
import os
from typing import Any
from pathlib import Path
from mcp.shared.exceptions import McpError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    ErrorData,
    GetPromptResult,
    Notification,
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
from subagents.speak import SPEAK_TOOL, SPEAK_TOOL_NAME, speak
from subagents.mcp_logging import LogContext

_log = LogContext("subagents")
console = _log.console

DEBUGGING = False

# DO NOT FOGET THE FUCKING TYPE ANNOTATION OR YOU ARE ASS RAPED
# pydantic happily deletes shit w/o a fucking care in the world (no logging, no errors, no nothing unless the person that built the model made it strict)
RawNotification_DeleteThisAndYouGetSurpriseAssRape = Notification[dict[str, Any], str]


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
    # Pre-install a file-backed handler on the root logger so framework logs
    # land on the log file instead of stderr.
    _log.configure_python_logging()

    server = Server("subagents")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [DELEGATE_TOOL, DELEGATE_STATUS_TOOL, DELEGATE_STOP_TOOL, SCREENCAP_TOOL, LOCATE_ANYTHING_TOOL, SPEAK_TOOL]
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

            if requested_tool == SPEAK_TOOL_NAME:
                return await speak(**arguments)

            if requested_tool == DELEGATE_STATUS_TOOL_NAME:
                task_id = arguments.get("task_id")
                return await delegate_status_tool(task_id)

            if requested_tool == DELEGATE_STOP_TOOL_NAME:
                task_id = arguments.get("task_id")
                return await delegate_stop_tool(task_id)

            if requested_tool != DELEGATE_TOOL_NAME:
                valid_tools = [DELEGATE_TOOL.name, DELEGATE_STATUS_TOOL.name, DELEGATE_STOP_TOOL.name, SCREENCAP_TOOL_NAME, LOCATE_ANYTHING_TOOL_NAME, SPEAK_TOOL_NAME]
                if DEBUGGING:
                    valid_tools.append(COUNT_TOOL_NAME)
                raise McpError(ErrorData(code=1, message=f"You made up a tool... you asked for {requested_tool}...", data={"valid_tools": valid_tools}))

            # Extract context for progress notifications
            ctx = server.request_context
            progress_token = ctx.meta.progressToken if ctx.meta else None

            # Build the on_tool_start callback that sends MCP progress notifications
            async def on_tool_start(tool_name: str, tool_args: dict, tool_start_count: int) -> None:
                # PRN add CLI arg to enable / disable verbosity of status?

                start_notification = RawNotification_DeleteThisAndYouGetSurpriseAssRape(
                    method="background/status",  # ? "task/status"
                    params={
                        "progressToken": progress_token,
                        # FYI progressToken is for the entire, upstream subagent (delegate) tool call, not the `tool_call.id` for this tool invocation in this subagent
                        "event": "tool_start",
                        "tool_name": tool_name,
                        "arguments": tool_args,
                        # "count": tool_start_count, # add if useful, i.e. if notifications get out of order for some reason?
                    },
                )

                if progress_token is None:
                    # client side might struggle to correlate, but I bet agents will figure it out
                    console.print("missing progress_token, responding w/ background/status anyways")

                await ctx.session.send_notification(start_notification)

            # Build the on_background_done callback that sends a progress
            # notification when a background subagent finishes. We capture the
            # session, progress_token, and request_id so the notification can be
            # sent after the tool call has already returned.
            async def on_background_done(result: str | None, error: str | None) -> None:
                if error is not None:
                    message = f"FAILED: Background subagent failed with error: {error}"
                else:
                    message = "DONE: Background subagent completed"

                final_notification = RawNotification_DeleteThisAndYouGetSurpriseAssRape(
                    method="background/done",
                    params={
                        "progressToken": progress_token,
                        "message": message,
                        # "related_request_id": str(ctx.request_id), # FYI I don't need this b/c I already have it in progressToken in my case => only add back if needed for some specific client issue
                        "result": result,
                        "error": error,
                    },
                )

                # * avoid vim.NIL (due to null/None values)
                if final_notification.params["result"] is None:
                    del final_notification.params["result"]
                if final_notification.params.get("error") is None:
                    del final_notification.params["error"]

                if progress_token is None:
                    # client side might struggle to correlate, but I bet agents will figure it out
                    console.print("missing progress_token, responding w/ background/done anyways")

                await ctx.session.send_notification(final_notification)

            # console.print(msg1.model_dump()) # testing the dump w/o waiting

            description = arguments.get("description", "")
            agent_type = arguments.get("agent_type", "general")
            recursion_limit = arguments.get("recursion_limit", DEFAULT_RECURSION_LIMIT)
            background = arguments.get("background", False)
            return await delegate_tool(
                description,
                agent_type,
                recursion_limit,
                on_tool_start=on_tool_start,
                background=background,
                on_background_done=on_background_done,
            )

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
