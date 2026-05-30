from asyncio import CancelledError
import asyncio
import os
from uuid import UUID
import rich
from rich.console import Console
from typing import Callable, Awaitable, Any
# log to a log tmp file
file = open('agent.log', 'a')
console = Console(file=file)

# might be helpful within your agent's tooling:
# import markdownify
# import readabilipy.simple_json

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain.tools import tool
from langchain_llama_server import ChatLlamaServer
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver
from mcp.types import TextContent, Tool
# from subagents.helpers.stream_messages import stream_messages
from deepagents.backends import LocalShellBackend
from langchain_mcp_adapters.client import MultiServerMCPClient

DELEGATE_TOOL_NAME = "delegate"
COUNT_TOOL_NAME = "count"

DELEGATE_TYPES = "assistant, web-researcher, command-runner, file-finder, test-finder"
# TODO add descriptions of the capabilities (briefly) or just let the name indicate that?
# TODO any desire to restrict tools for subagents so they can't be used for other purposes? for now I will give out the same tools until it causes issues.

COUNT_TOOL = Tool(
    name=COUNT_TOOL_NAME,
    description="Count to, for testing progress and cancellation",
    inputSchema={
        'type': 'object',
        'properties': {
            'to': { 'type': 'integer', 'description': 'the integer to count to', },
        },
        'required': ['to'],
    }
) 

DELEGATE_TOOL = Tool(
    name=DELEGATE_TOOL_NAME,
    description="Delegate to a subagent to perform relevent tasks and summarize findings.",
    inputSchema={
        'properties': {
            'description': {
                'description': 'describe the task for the agent to perform',
                'type': 'string'
            },
            'agent_type': {
                'description': 'which subagent profile to use, options: ' + DELEGATE_TYPES,
                'type': 'string',
            }
            # TODO other options? limit # turns (recursion_limit?)
        },
        'required': ['description'],
        'type': 'object'
    })

async def setup_agent():
    global agent, client, extra_tools, model  # FYI need agent aside from GC issues
    client = MultiServerMCPClient({
        "fetch": {
            # I like my mods to fetch so just use it!
            #  also might feel "wrong" that I already have fetch in ask-openai.nvim... but that's for supervisor! this makes fetch avail for subagents to go crazy and then report back a concise response
            "transport": "stdio",
            "command": "uvx",
            "args": [
                "--directory",
                os.environ["HOME"] + "/repos/github/g0t4/mcp-servers/src/fetch",
                "mcp-server-fetch",
            ],
        },
    })
    mcp_tools = await client.get_tools()
    extra_tools = mcp_tools  # PRN extend beyond just MCP
    # TODO configurable tools per agent_type (lazy create agents w/ hardcoded lookup for tools)
    #  TODO add tools arg to tool too? so supervisor agent (MCP client) can pass what tools to provide? from predefined list

    model = ChatLlamaServer(base_url="http://ask.lan:8012", api_key="foo")
    agent = create_deep_agent(
        model,
        checkpointer=InMemorySaver(),
        backend=LocalShellBackend(virtual_mode=False),
        # TODO how about limit dir to CWD only? Or pass a dir as an argument in main()
        tools=extra_tools,
    )


async def delegate_tool(
    description: str,
    agent_type: str | None,
    on_tool_start: Callable[[str, dict, int], Awaitable[None]] | None = None,
):
    """
    Delegate to a subagent to perform a task and summarize findings.

    Args:
        description: The task description to delegate.
        agent_type: Which subagent profile to use.
        on_tool_start: Optional async callback invoked for each tool start event.
                       Signature: (tool_name: str, tool_args: dict, tool_start_count: int) -> None
    """
    async def _inner_delegate_tool(
        desc: str,
        a_type: str | None,
        tool_start_cb: Callable[[str, dict, int], Awaitable[None]] | None,
    ) -> list[TextContent]:
        console.print("START")

        # quick hack to get messages by providing thread_id to in memory store
        #   just for duration of a single request
        config: RunnableConfig = {"configurable": {"thread_id": None}}

        # Use HumanMessage objects for proper LangChain integration
        user_prompt = description + """\n\n## APPROACH
    You are acting in an official sub-agent capactity.
    The user expects you to try again if something fails. That means a different set of arguments to a tool. Or a different tool. Whatever can achieve the requested outcome.
    Do not just try one tool call and then stop with the result. Unless it is successful, then by all means stop there! 
    """

        messages = [HumanMessage(content=user_prompt)]

        # Run the agent with astream_events for clean event handling
        final_ai_content = ""
        tool_start_count = 0
        invoke_input = {"messages": messages}

        async for event in agent.astream_events(invoke_input, config=config, version="v2"):
            event_type = event.get("event", "")
            
            if event_type == "on_tool_start":
                tool_start_count += 1
                tool_name = event.get("name", "unknown")
                tool_inputs = event.get("data", {}).get("input", {})

                # Log tool start to agent.log (via rich console)
                console.print(f"tool_start=[tool={tool_name}] args={tool_inputs}")

                # Invoke the progress callback if provided (passing the current count)
                if tool_start_cb is not None:
                    try:
                        await tool_start_cb(tool_name, tool_inputs, tool_start_count)
                    except Exception:
                        pass  # best effort, don't break tool execution on callback errors

            elif event_type == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk", None)
                if chunk and hasattr(chunk, 'content') and chunk.content:
                    final_ai_content += chunk.content

        console.print("DONE")
        output = await agent.aget_state(config)
        out_messages = output.values.get("messages", [])
        last_message = out_messages[-1] if out_messages else None
        console.print("output", output)

        # Return the accumulated AI response, or fall back to the last message content
        response_content = final_ai_content if final_ai_content else (last_message.content if last_message else "")
        return [TextContent(type="text", text=response_content)]

    try:
        return await _inner_delegate_tool(description, agent_type, on_tool_start)
    except asyncio.CancelledError:
        # TODO cancel the request... need to implement astream_events most likely and cancel on start of next tool call?
        console.print("CancelledError caught in delegate_tool")
        raise

# (optionally add interrupt support for approvals) PRN... what if the supervisor does the approvals? IOTW... subagent asks for any sensitive tool call request and supervisor agent has to respond to approve it?
#  AiITL middleware ;) SITL (supervisor in the loop) middleware
