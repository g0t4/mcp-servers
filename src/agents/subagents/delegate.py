from asyncio import CancelledError
import asyncio
import json
import os
from uuid import UUID, uuid4
from pathlib import Path
from langchain.agents import create_agent
from rich.console import Console
from rich.panel import Panel
from typing import Callable, Awaitable, Any

# XDG-compliant log path: $XDG_STATE_HOME/mcp-servers/agent.log (falls back to ~/.local/state/mcp-servers/agent.log)
_xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
_log_dir = Path(_xdg_state) / "mcp-servers"
_log_dir.mkdir(parents=True, exist_ok=True)
_traces_dir = _log_dir / "traces"
_traces_dir.mkdir(parents=True, exist_ok=True)
_log_file = open(_log_dir / "agent.log", "a")
console = Console(file=_log_file, force_terminal=True)

# might be helpful within your agent's tooling:
# import markdownify
# import readabilipy.simple_json

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain.tools import tool
from langchain_llama_server import ChatLlamaServer
from langgraph.checkpoint.memory import InMemorySaver
from mcp.types import TextContent, Tool
from deepagents.backends import LocalShellBackend
from langchain_mcp_adapters.client import MultiServerMCPClient

DELEGATE_TOOL_NAME = "delegate"

DELEGATE_TYPES = "assistant, web-researcher, command-runner, file-finder, test-finder"
# TODO add descriptions of the capabilities (briefly) or just let the name indicate that?
# TODO any desire to restrict tools for subagents so they cannot be used for other purposes? for now I will give out the same tools until it causes issues.

DEFAULT_RECURSION_LIMIT = 50

RECURSION_LIMIT_DESC = ("LangChain's max messages for the subagent before stopping. Each subagent tool call "
                        "produces 2 messages (input + output). Leave unset unless the task needs more or "
                        "fewer than the default (50).")

DELEGATE_TOOL = Tool(name=DELEGATE_TOOL_NAME,
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
                             },
                             'recursion_limit': {
                                 'description': RECURSION_LIMIT_DESC,
                                 'type': 'integer',
                             }
                         },
                         'required': ['description'],
                         'type': 'object'
                     })

async def setup_agent():
    global agent, client, tools, model  # FYI need agent aside from GC issues
    client = MultiServerMCPClient({
        "fetch": {
            # I like my mods to fetch so just use it!
            #  also might feel "wrong" that I already have fetch in ask-openai.nvim... but that's for supervisor! this makes fetch avail for subagents to go crazy and then report back a concise response
            "transport": "stdio",
            "command": "uv",
            "args": [
                "run",
                "--directory",
                os.environ["HOME"] + "/repos/github/g0t4/mcp-servers/src/fetch",
                # DO not run official pypi version as it has a STUPID robots.txt check ON EVERY GODDAMN FETCH ... 2 calls per plus it shits a brick if someone sez no your agent no my special site
                "mcp-server-fetch",
            ],
        },
        "run_process": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                os.environ["HOME"] + "/repos/github/g0t4/mcp-server-commands/build/index.js",
                # -- FYI leave --verbose on for now given I am using a log file so it s/b NBD
                # --    this will be a huge help in troubleshooting hung tool calls and other issues
                "--verbose",
            ]
        }
    })
    mcp_tools = await client.get_tools()
    console.print("mcp_tools", mcp_tools)
    tools = mcp_tools  # PRN extend beyond just MCP

    model = ChatLlamaServer(base_url="http://ask.lan:8012", api_key="foo")
    agent = create_agent(
        model,
        checkpointer=InMemorySaver(),
        # TODO how about limit dir to CWD only? Or pass a dir as an argument in main()
        tools=tools,
    )

async def delegate_tool(
    description: str,
    agent_type: str | None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    on_tool_start: Callable[[str, dict, int], Awaitable[None]] | None = None,
):
    """
    Delegate to a subagent to perform a task and summarize findings.

    Args:
        description: The task description to delegate.
        agent_type: Which subagent profile to use.
        recursion_limit: Maximum tool call steps before stopping (default: 50).
        on_tool_start: Optional async callback invoked for each tool start event.
                       Signature: (tool_name: str, tool_args: dict, tool_start_count: int) -> None
    """

    async def _inner_delegate_tool(
        desc: str,
        a_type: str | None,
        r_limit: int,
        tool_start_cb: Callable[[str, dict, int], Awaitable[None]] | None,
    ) -> list[TextContent]:
        console.print(Panel("START", style="bold green"), highlight=True)

        # Generate a unique trace file per subagent run
        trace_id = uuid4().hex[:12]
        timestamp = asyncio.get_event_loop().time()
        trace_file = _traces_dir / f"{timestamp:.6f}-{trace_id}.json"

        # Initialize trace with metadata
        trace_data: dict[str, Any] = {
            "trace_id": trace_id,
            "timestamp": timestamp,
            "agent_type": a_type,
            "recursion_limit": r_limit,
            "description": description,
            "events": [],
        }

        # Track AI response chunks for final output
        last_ai_content = ""
        last_ai_reasoning = ""
        tool_start_count = 0
        current_ai_messages: list[dict[str, Any]] = []

        def _write_trace_event(event: dict[str, Any]) -> None:
            """Append an event to the trace file immediately."""
            trace_data["events"].append(event)
            with open(trace_file, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")

        # quick hack to get messages by providing thread_id to in memory store
        #   just for duration of a single request
        config: RunnableConfig = {"recursion_limit": r_limit, "configurable": {"thread_id": None}}

        # Use HumanMessage objects for proper LangChain integration
        user_prompt = description + """\n\n## APPROACH
    You are acting in an official sub-agent capactity.
    The user expects you to try again if something fails. That means a different set of arguments to a tool. Or a different tool. Whatever can achieve the requested outcome.
    Do not just try one tool call and then stop with the result. Unless it is successful, then by all means stop there!
    """

        messages = [HumanMessage(content=user_prompt)]
        invoke_input = {"messages": messages}

        # Capture the initial user message in the trace
        _write_trace_event({
            "type": "user_message",
            "content": user_prompt,
        })

        # Run the agent with astream_events for clean event handling
        _agent_completed_successfully = False
        _event_loop_exception: Exception | None = None
        try:
            async for event in agent.astream_events(invoke_input, config=config, version="v2"):
                event_type = event.get("event", "")
                event_name = event.get("name", "")

                if event_type == "on_tool_start":
                    tool_start_count += 1
                    tool_name = event.get("name", "unknown")
                    tool_inputs = event.get("data", {}).get("input", {})

                    # Log tool start to agent.log (via rich console)
                    console.print(f"  [bold yellow]tool_start[/] [cyan]{tool_name}[/] [dim]args={tool_inputs}[/]")

                    # Write to trace
                    _write_trace_event({
                        "type": "tool_start",
                        "tool": tool_name,
                        "inputs": tool_inputs,
                        "step": tool_start_count,
                    })

                    # Invoke the progress callback if provided (passing the current count)
                    if tool_start_cb is not None:
                        try:
                            await tool_start_cb(tool_name, tool_inputs, tool_start_count)
                        except Exception:
                            pass  # best effort, don't break tool execution on callback errors

                elif event_type == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    tool_output = event.get("data", {}).get("output", None)
                    console.print(f"  [dim]tool_end[/] [cyan]{tool_name}[/] [gray]output_len={len(str(tool_output)) if tool_output else 0}[/]")

                    _write_trace_event({
                        "type": "tool_end",
                        "tool": tool_name,
                        "output": tool_output,
                        "step": tool_start_count,
                    })

                elif event_type == "on_tool_error":
                    tool_name = event.get("name", "unknown")
                    error = event.get("error", None)
                    console.print(f"  [bold red]tool_error[/] [cyan]{tool_name}[/] [red]{error}[/]")

                    _write_trace_event({
                        "type": "tool_error",
                        "tool": tool_name,
                        "error": str(error),
                        "step": tool_start_count,
                    })

                elif event_type == "on_chat_model_start":
                    # Track when the model begins generating
                    current_ai_messages.append({
                        "type": "ai_generation_start",
                        "model": event_name,
                    })
                    last_ai_content = ""
                    last_ai_reasoning = ""

                elif event_type == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk", None)
                    if chunk:
                        console.print("chunk", chunk)
                        if hasattr(chunk, 'content') and chunk.content:
                            last_ai_content += chunk.content
                        if hasattr(chunk, 'additional_kwargs'):
                            last_ai_reasoning += chunk.additional_kwargs.get('reasoning_content', '')
                        # FYI could accumulate reasoning per AI message... to see what drove tool call decisions
                    # AIMessageChunk(
                    #     content='',
                    #     additional_kwargs={'reasoning_content': ' a'},
                    #     response_metadata={'model_provider': 'llama_server'},
                    #     id='lc_run--019ecd5d-e866-7fc0-8c73-6be7e584082d',
                    #     tool_calls=[],
                    #     invalid_tool_calls=[],
                    #     tool_call_chunks=[]
                    # )

                elif event_type == "on_chat_model_end":
                    # Track AI generation completion
                    current_ai_messages.append({
                        "type": "ai_generation_end",
                        "model": event_name,
                        "content": last_ai_content or "",
                        "reasoning": last_ai_reasoning or "",
                    })
                    if current_ai_messages:
                        _write_trace_event({
                            "type": "ai_messages",
                            "messages": current_ai_messages,
                        })
                        current_ai_messages = []

            _agent_completed_successfully = True

        except Exception as error:
            _event_loop_exception = error
            raise

        finally:
            # Always finalize the trace, regardless of success/failure
            if _event_loop_exception is not None:
                final_status = "failed"
                error_message = str(_event_loop_exception)
            else:
                final_status = "success"
                error_message = ""

            _write_trace_event({
                "type": "agent_complete",
                "status": final_status,
                "total_tools_called": tool_start_count,
                "error": error_message if error_message else None,
            })

        console.print(Panel("DONE", style="bold green"), highlight=True)
        output = await agent.aget_state(config)
        # TODO something is wronger than wrong here... I need too look into WTF is going on failures here...
        #  failure = null response for final message below
        out_messages = output.values.get("messages", [])
        last_message = out_messages[-1] if out_messages else None
        console.print(f"[dim]output[/] [gray]{output}[/]")
        console.print(f"[dim]final_ai_content[/] {last_ai_content}")
        console.print(f"[dim]last_message[/] {last_message}")

        # Return the accumulated AI response, or fall back to the last message content
        response_content = last_ai_content if last_ai_content else (last_message.content if last_message else "")
        return [TextContent(type="text", text=response_content)]

    try:
        return await _inner_delegate_tool(description, agent_type, recursion_limit, on_tool_start)
    except asyncio.CancelledError:
        # TODO cancel the request... need to implement astream_events most likely and cancel on start of next tool call?
        console.print(Panel("CancelledError caught in delegate_tool", style="bold red"), highlight=True)
        raise
    except Exception as error:
        console.print(Panel(f"ERROR: {error}", style="bold red"), highlight=True)
        raise

# (optionally add interrupt support for approvals) PRN... what if the supervisor does the approvals? IOTW... subagent asks for any sensitive tool call request and supervisor agent has to respond to approve it?
#  AiITL middleware ;) SITL (supervisor in the loop) middleware
