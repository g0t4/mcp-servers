import os
import rich
from rich.console import Console
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

DELEGATE_TOOL = "delegate"

DELEGATE_DEFINITION = Tool(
    name=DELEGATE_TOOL,
    description="Delegate to a subagent to perform research and summarize findings.",
    inputSchema={
        'properties': {
            'description': {
                'description': 'describe the task for the agent to perform',
                'type': 'string'
            },
            'agent_type': {
                'description': 'which subagent profile to use, options: general, web-researcher, command-runner',
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

async def delegate_tool(description: str, agent_type: str | None):
    console.print("START")
    # print(f"launching subagent {agent_type} with {description}")
    # quick hack to get messages by providing thread_id to in memory store
    #   just for duration of a single request
    config: RunnableConfig = {"configurable": {"thread_id": None}}
    messages = [HumanMessage(description 
        + """\n\n## APPROACH
You are acting in an official sub-agent capactity.
The user expects you to try again if something fails. That means a different set of arguments to a tool. Or a different tool. Whatever can achieve the requested outcome.
Do not just try one tool call and then stop with the result. Unless it is successful, then by all means stop there! 
"""
        # TODO setup tool calling loop until response is achieved or model actually gives up.

                             )],
    # PRN accept recursion_limit arg?
    # await stream_messages(agent, messages, config=config) # TODO use a log file and stream to log file
    # TODO also capture the final output from LangGraph chain to return to user
    output = await agent.ainvoke({"messages": {"role": "user", "content": description}}, config=config)
    # thread = agent.get_state(config).values["messages"]
    # last_message = thread[-1]
    console.print("DONE")
    out_messages = output["messages"]
    last_message = out_messages[-1]
    # TODO better handling of output => response
    #  don't just assume it's an AIMessage
    console.print("output", output)
    return [TextContent(type="text", text=last_message.content)]

# (optionally add interrupt support for approvals) PRN... what if the supervisor does the approvals? IOTW... subagent asks for any sensitive tool call request and supervisor agent has to respond to approve it?
#  AiITL middleware ;) SITL (supervisor in the loop) middleware
