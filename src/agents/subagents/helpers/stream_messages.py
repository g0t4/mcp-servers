import traceback
import os
import rich
from rich.console import RenderableType, Console
from rich.style import Style
from rich.syntax import Syntax
from rich.padding import Padding
from rich.live import Live
from rich.text import Text
from rich.tree import Tree
from rich.pretty import Pretty
from rich.markup import escape
from rich.markdown import Markdown
from flair.rich_theme.markdown import md_theme

import json
import sys
import asyncio
from dataclasses import dataclass
from typing import Any, Sequence, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage, AIMessageChunk
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.runnables.schema import EventData, StreamEvent
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

def show_messages(messages):
    # *legacy* (initial helper to show messages... not related to stream_messages() below)
    console = Console(theme=md_theme)
    # first pass at showing messages (pre-streaming)
    # this is replaced by stream_messages() below which is way more useful (i.e. streaming)
    for m in messages:
        if isinstance(m, HumanMessage):
            console.print(f'[bold slate_blue1]Human[/]', end="")
            print(f': {m.content}')
        elif isinstance(m, AIMessage):
            if m.content:
                console.print(f'[bold deep_sky_blue3]AI[/]', end="")
                print(f': {m.content}')
            if m.tool_calls:
                for call in m.tool_calls:
                    console.print(f'[bold deep_sky_blue3]Tool call[/]', end="")
                    name = call["name"]
                    print(": " + name, end="")
                    if name == "run_python":
                        print()
                        code = call["args"]["code"]
                        syntax = Syntax(code, "python", theme="monokai")
                        console.print(Padding(syntax, pad=(0, 0, 0, 4)))
                    elif name == "run_command":
                        print()
                        code = call["args"]["commandline"]
                        syntax = Syntax(code, "bash", theme="monokai")
                        console.print(Padding(syntax, pad=(0, 0, 0, 4)))
                    elif name == "apply_patch":
                        print()
                        patch = call["args"]["patch"]
                        syntax = Syntax(call["args"]["patch"], "diff", theme="monokai")
                        console.print(Padding(syntax, pad=(0, 0, 0, 4)))
                    else:
                        print(f'({call["args"]})')

        elif isinstance(m, ToolMessage):
            console.print(f'[bold slate_blue1]Tool[/]', end="")
            print(f': {m.content}')
        else:
            console.print(f"unexpected message type: {m}")

def show_approval_interrupts(event: StreamEvent, tree: "TreeWrapper"):
    # trigger HITL approvals
    #   use gptoss for one at a time
    #   use Qwen3.6 for parallel tool calls w/ two approvals arriving together
    # Interrupt(
    #     value={
    #         'action_requests': [
    #             {
    #                 'name': 'run_command',
    #                 'args': {'commandline': 'hostname'},
    #                 'description': "Tool execution pending approval\n\nTool: run_command\nArgs:
    # {'commandline': 'hostname'}"
    #             },
    #             {
    #                 'name': 'run_command',
    #                 'args': {'commandline': 'date'},
    #                 'description': "Tool execution pending approval\n\nTool: run_command\nArgs:
    # {'commandline': 'date'}"
    #             }
    #         ],
    #         'review_configs': [
    #             {
    #                 'action_name': 'run_command',
    #                 'allowed_decisions': ['approve', 'edit', 'reject']
    #             },
    #             {
    #                 'action_name': 'run_command',
    #                 'allowed_decisions': ['approve', 'edit', 'reject']
    #             }
    #         ]
    #     },
    #     id='8784b505500ed4b71d24ba3105d43dfc'
    # )
    data: EventData = event.get("data")
    chunk = data.get("chunk")
    if not chunk:
        return
    if not isinstance(chunk, dict):
        return

    __interrupt__ = chunk.get("__interrupt__")
    if not __interrupt__:
        return

    for interrupt in __interrupt__:
        node = tree.add_markup("[bold gray0 on deep_pink2] APPROVAL NEEDED [/]")
        node.blank_line()
        # node.add_pretty(interrupt) # dump interrupt object (like above)
        actions = interrupt.value.get("action_requests", [])
        review_configs = interrupt.value.get("review_configs", [])
        assert len(actions) == len(review_configs)
        # actions and review configs correspond
        for idx, (request, config) in enumerate(zip(actions, review_configs), start=1):
            # description = request.get('description')  # description usually is just prefix + tool name + args, I'm going to go directly to name/args and use those
            name = request.get('name')
            args = request.get('args', {})
            #
            action_name = config.get('action_name')
            allowed = ', '.join(config.get('allowed_decisions', []))
            approval_node = node.add_markup(f"{idx}. [bold]{name}[/]")
            if args:
                if name.startswith("run_python"):
                    code = args.get("code")
                    approval_node.add_syntax(code, "python")
                elif name.startswith("run_command"):
                    commandline = args.get("commandline")
                    approval_node.add_syntax(commandline, "bash")
                elif name == "apply_patch":
                    patch = args.get("patch")
                    approval_node.add_syntax(patch, "diff")
                else:
                    approval_node.add_pretty(args)
            approval_node.add_markup(f"[italic]{allowed}[/]")
            approval_node.blank_line()

class TreeWrapper(Tree):
    """ Thin wrapper around :class:`rich.tree.Tree` with additional helpers. """

    parent: "TreeWrapper | None" = None

    def blank_line(self) -> None:
        BLANK_LINE = ""

        if not self.children:
            self.add(BLANK_LINE)
            return

        last = self.children[-1]
        label = last.label
        if isinstance(label, Text):
            ends_newline = label.plain.endswith("\n")
        else:
            ends_newline = isinstance(label, str) and label.endswith("\n")
        if not ends_newline:
            self.add(BLANK_LINE)

    def add(self, *renderables, **kwargs) -> "TreeWrapper":
        """ make sure child trees are all TreeWrapper type too """
        node = super().add(*renderables, **kwargs)
        if not isinstance(node, TreeWrapper):
            node.__class__ = TreeWrapper
        node.parent = self
        return node

    def add_no_markup(self, text: str, **kwargs) -> "TreeWrapper":
        """ make explicit this content should not have markup rendered """
        # btw Text == plain unless you pass a style arg
        return self.add(Text(text), **kwargs)

    def add_markup(self, label: str, **kwargs) -> "TreeWrapper":
        """ this is purely for readability, to make it clear that the content should have markup rendered """
        return self.add(label, **kwargs)

    def add_pretty(self, obj: Any, **kwargs) -> "TreeWrapper":
        return self.add(Pretty(obj), **kwargs)

    def add_syntax(self, code: str, lexer: str, *, theme: str = "monokai", **kwargs) -> "TreeWrapper":
        return self.add(Syntax(code, lexer, theme=theme), **kwargs)

    def add_sections_from_json_keys(self, json_str: str, **kwargs) -> "TreeWrapper":
        try:
            obj = json.loads(json_str)
        except json.JSONDecodeError as error:
            # Show the error and raw content in the tree
            self.add_error("failed to load JSON result", error) \
                .add_no_markup(json_str)
            return self

        for key, value in obj.items():
            # Create a node for the key and attach the value as a child
            self.add_section(key, value)
        return self

    def show_truncated_string(self, text: str):
        lines = text.splitlines()
        max_lines = 5
        if len(lines) > max_lines:
            initial = "\n".join(lines[:max_lines])
            truncated_lines = len(lines) - max_lines
            truncated_chars = len(text) - len(initial)  # total chars - shown chars
            truncated_indicator = f"... ({truncated_lines} lines, {truncated_chars} chars)"
            # PRN truncate on char count too? really long lines of output can be a problem too (measure length of first X lines and if super long then take char_max too... else maybe allow more lines than I do now within reason)
            self.add_no_markup(initial)
            self.add_markup(f"[bold yellow]{truncated_indicator}[/]")
        else:
            self.add_no_markup(text)

    def add_section(self, title: str, value: Any):
        section = self.add_markup(f"[bold]{title}[/]:")
        if isinstance(value, str):
            section.show_truncated_string(value)
        else:
            section.add_no_markup(str(value))

    def remove_self(self):
        if self.parent:
            try:
                self.parent.children.remove(self)
            except ValueError as error:
                # ? do I want to see this failure, ever?
                self.add_error("unexpected, failed to remove self", error)
            self.parent = None
        return self

    def add_error(self, message: str, error: Exception) -> "TreeWrapper":
        node = self.add_markup(f"[red bold]{message}[/]")
        node.add_pretty(error)
        node.add_no_markup(traceback.format_exc())
        return node

@dataclass
class StreamingChunksState:
    node: TreeWrapper | None = None
    accumulated: AIMessageChunk | None = None

    def reset(self):
        """reset state for a new model response"""
        self.node = None
        self.accumulated = None

async def stream_messages(
    agent: Runnable,
    input: Any | list[BaseMessage] | Command | None,  # Any: for {"messages": list[BaseMessage] }
    *,
    dump_events=False,
    only_dump_events=False,
    config: RunnableConfig | None = None,
    **kwargs,
):
    # TODO remove message count so I don't have to worry about decrement (i.e. RemoveMessage, summarization), Commands, and subagents
    message_count = 0

    def increment_message_count():
        nonlocal message_count
        message_count += 1

    def show_system_message(message: SystemMessage, tree: TreeWrapper):
        child = tree.add_markup(f"{message_count}. [bold gray0 on gold1] SystemMessage [/]")
        child.add_no_markup(message.content)
        child.blank_line()

    def show_human_message(message: HumanMessage, tree: TreeWrapper):
        child = tree.add_markup(f"{message_count}. [bold gray0 on slate_blue1] HumanMessage [/]")
        child.add_no_markup(message.content)
        child.blank_line()

    def _dict_to_message(message: dict) -> BaseMessage:
        # FYI supported "role" strings: 'human', 'user', 'ai', 'assistant', 'function', 'tool', 'system', or 'developer'
        role = message.get("role")
        if role in ("human", "user"):
            return HumanMessage(**message)
        if role in ("ai", "assistant"):
            return AIMessage(**message)
        # PRN add support for role="function" ... probably can shoehorn into ToolMessage... but I also might want to show it as a separate message type
        if role == "tool":
            return ToolMessage(**message)
        if role in ("system", "developer"):
            return SystemMessage(**message)
        raise ValueError(f"Unsupported role: {role}")

    def _show_message(message, tree: TreeWrapper, event: StreamEvent | None = None):
        if isinstance(message, dict):
            message = _dict_to_message(message)

        # PRN pass event to other formatters? right now only show_tool_message needs it, but you can pass to others too (make optional)
        if isinstance(message, HumanMessage):
            show_human_message(message, tree)
        elif isinstance(message, SystemMessage):
            show_system_message(message, tree)
        elif isinstance(message, ToolMessage):
            show_tool_message(message, tree, event)
        elif isinstance(message, AIMessage):
            show_ai_message(message, tree)
        else:
            # do not raise b/c I use show_message for several scenarios beyond just initial messages... killing mid trace would not be fun
            branch = tree.add_markup(f"[red]Unsupported message type: {type(message).__name__}[/]")
            branch.add_pretty(message)
            branch.blank_line()

    def on_tool_start(event: StreamEvent, tree: TreeWrapper) -> TreeWrapper:
        try:
            tool_name = event["name"]
            data: EventData = event.get("data")
            args = data.get("input", {})

            # AFAICT no tool_call_id available in on_tool_start
            # - show.show_tools() => rich.inspect(deepagents.middleware.filesystem.LsSchema)

            # FYI some of the args are intuitive when you see the ToolMessage output

            # * prettify select arguments (i.e. multiline strings that are harder to read in thje JSON like dump from AIMessage tool calls)
            #   also useful to make the call standout and easy at a glance (i.e. read_file foo/to/bar.txt)
            #   DO NOT REPEAT all ARGS (i.e. don't need timeout on commands, offset/limit on read_file, etc)

            color = "[bold medium_spring_green]"

            # * title (first are tools that have custom titles)
            if tool_name == "task":
                subagent_type = args.get("subagent_type")
                node = tree.add_markup(f"{color}Delegating {tool_name} to {subagent_type}")
                # description arg => will show in HumanMessage below so no need to repeat it here
                #
                # FYI can add guides for subagents if create new tree w/ them enabled (default on)
                # - I would do this but then I don't want guides on every level thereafter
                #   which would require a new tree per child node under the subagent node (this new_tree)...
                #   and that would get messy ... I'd probably have to make a new tree for every node!
                #   not sure if that would have any issues too!
                # # uncomment this code to get guides on everything below subagent:
                # new_tree = TreeWrapper(f"{color}Delegating {tool_name} to {subagent_type}")
                # tree.add(new_tree)
                # node = new_tree
                #
            elif tool_name == "ls":
                path = args.get("path")
                node = tree.add_markup(f"{color}{tool_name} {path}")
            elif tool_name == "glob":
                pattern = args.get("pattern")
                path = args.get("path")
                node = tree.add_markup(f"{color}{tool_name} {pattern} (in {path})")
            elif tool_name == "write_file":
                file_path = args.get("file_path")
                node = tree.add_markup(f"{color}{tool_name} {file_path}")
                content = args.get("content")
                ext = os.path.splitext(file_path)[1].lstrip(".")
                node.add_syntax(content, ext)
            elif tool_name == "read_file":
                # PRN args offset, limit
                file_path = args.get("file_path")
                node = tree.add_markup(f"{color}{tool_name} {file_path}")
            elif tool_name == "edit_file":
                file_path = args.get("file_path")
                node = tree.add_markup(f"{color}{tool_name} {file_path}")
                old = args.get("old_string")
                new = args.get("new_string")
                # build diff (prepend -/+ to old/new respectively)
                diff = [f"-{line}" for line in old.splitlines()]
                diff += [f"+{line}" for line in new.splitlines()]
                diff = "\n".join(diff)
                node.add_syntax(diff, "diff")
                # ? or show old/new separate?
                # skip replace_all arg
            # elif tool_name == "grep":
            #     # pattern (not regex), path, glob, output_mode
            # elif tool_name == "write_todos":
            #     pass  # ? TODO?
            else:
                # generic title
                node = tree.add_markup(f"{color}Calling {tool_name}")

            # * prettify select arguments (basically for things that use the generic title)
            assert isinstance(args, dict)
            if tool_name.startswith("run_python"):
                code = args.get("code")
                node.add_syntax(code, "python")
            elif tool_name.startswith("run_command"):
                commandline = args.get("commandline")
                node.add_syntax(commandline, "bash")
            elif tool_name == "execute":
                command = args.get("command")
                node.add_syntax(command, "bash")
            elif tool_name == "apply_patch":
                patch = args.get("patch")
                node.add_syntax(patch, "diff")
            # do not show other tools/args that I don't have custom formatter for b/c they already show from AIMessage
        except Exception as error:
            # lots of novel logic... would be terrible to trip this at random and kill a trace
            #   (i.e. b/c a tool argument name is wrong)
            #   or maybe issues with unsupported language and Syntax
            node = tree.add_error("Failed to build Calling tool summary", error)

        node.blank_line()
        return node  # return only for register_node, see notes in on_too_start handler, I am skeptical this is ever used.

    def _display_tool_message_content(message: ToolMessage, tree: TreeWrapper):
        content = message.content
        if message.name == "run_command":
            _display_tool_message_for_run_command(message, tree)
        # elif message.name == "run_python":
        #    _display_tool_run_python(message, tree)
        elif isinstance(content, str):
            tree.show_truncated_string(content)
        else:
            tree.add_pretty(content)  # pretty spans multiple lines, is indented, looks very nice

    def _display_tool_message_for_run_command(message: ToolMessage, tree: TreeWrapper):
        if not isinstance(message.content, str):
            tree.add_markup('[red]run_command message.content should be a string, but is not:[/]')
            tree.add_pretty(message.content)
            return
        tree.add_sections_from_json_keys(message.content)

    def show_tool_message(message: ToolMessage, tree: TreeWrapper, event: StreamEvent | None = None):

        def get_tool_name(message: ToolMessage, event: StreamEvent | None) -> str:
            name = message.name
            if name is not None:
                return name
            if event and event.get("name"):
                # happens for `task` and `write_todos` via Command(update=...)
                return event.get("name")
            return "MISSING TOOL NAME"

        tool_name = get_tool_name(message, event)
        show_tool_name = "task (SUBAGENT)" if tool_name == "task" else tool_name
        id = message.tool_call_id

        # FYI status should only be success/error, leave as-is if not:
        status = "✅" if message.status == "success" else ("❌" if message.status == "error" else message.status)

        child = tree.add_markup(f"{message_count}. [bold gray0 on slate_blue1] ToolMessage [/]: [bold]{show_tool_name}[/] {status} ({id})")
        # ? if tool_name == "task" => show message.content as markdown?
        _display_tool_message_content(message, child)
        child.blank_line()
        # FYI I could show the args pretty-ified here if I cache them and don't show on tool start

    def on_tool_end(event: StreamEvent, tree: TreeWrapper):
        data: EventData = event.get("data")
        output = data.get("output")
        if isinstance(output, ToolMessage):
            increment_message_count()
            show_tool_message(output, tree, event)
        elif isinstance(output, Command):
            show_command(output, tree, event)
        # PRN resume, goto (anything to show for these?)
        else:
            # show other modified channels:
            # `task` tool can update more than just the "messages" (ToolMessage)... also "files" are shared
            tree.add_pretty(output)  # FYI I actually like seeing the object, that seems good enough for now...

    def show_command(command: Command, tree: TreeWrapper, event: StreamEvent):
        child = tree.add_markup(f"[bold gray0 on magenta3] Command [/]")
        if command.update:
            messages = command.update.get("messages", [])
            for msg in messages:
                # i.e. ToolMessage (task report/summary) from subagent
                increment_message_count()
                _show_message(msg, child, event)
            for key, value in command.update.items():
                # add other formatters for other common channels as they need arises (i.e. files channel)
                if key == "messages":
                    continue
                child.add_markup(f"{key}:")
                child.add_pretty(value)
        child.blank_line()

    def show_ai_message(message: AIMessage, tree: TreeWrapper):
        child = tree.add_markup(f"{message_count}. [bold gray0 on deep_sky_blue3] AIMessage [/]")
        reasoning = message.additional_kwargs.get("reasoning_content")
        if reasoning:
            reasoning_node = child.add_markup("[bold]reasoning:[/]")
            reasoning_node.add(Text(reasoning, style="italic"))  # FYI Text does not parse/apply markup in the text value (1st positional arg)... use style to apply to the entire text value
            reasoning_node.blank_line()
        if message.content:
            content_node = child.add_markup("[bold]content:[/]")
            # PRN any situations where this isn't markdown? that would be materially ruined by trying to use it as markdown? i.e. structured outputs?
            # PRN also apply to task tool's ToolMessage.content? likely is markdown too as that is the final AIMessage from subagent
            content_node.add(Markdown(message.content))
        if message.tool_calls:
            for call in message.tool_calls:
                name = call.get("name")
                id = call.get("id", "")
                tool_tree = child.add_markup(f"[bold]{name}[/] ({id})")
                args = call.get("args", "")
                if args:
                    tool_tree.add_pretty(args)
                tool_tree.blank_line()
        return child

    def on_chat_model_stream(event: StreamEvent, state: StreamingChunksState, tree: TreeWrapper):
        # streaming chunks so we can see response as it is generated
        chunk = event.get("data").get("chunk")
        assert isinstance(chunk, AIMessageChunk)

        # * accumulate chunks
        if not state.accumulated:
            increment_message_count()
            state.accumulated = chunk
        else:
            # accumulated holds cumulative chunks => effectively becomes AIMessage (handles reasoning/content/tool_calls chunking)
            state.accumulated = state.accumulated + chunk

        # * replace node
        if state.node:
            state.node.remove_self()
        state.node = show_ai_message(state.accumulated, tree)
        # state.node.add_pretty(state.accumulated) # DEBUGGING: fixed structure fills out as each chunk arrives (looks cool)

    def dump_all_events_except_streaming_tokens_for_debugging(event: StreamEvent, tree: TreeWrapper):
        event_type = event["event"]
        if event_type in {"on_chat_model_stream"}:
            return
        tree.add_pretty(event)

    def dump_tree_to_out_ansi(tree: TreeWrapper):
        # btw this worked good to scroll this out.ansi file in a trace like fashion (flashy but works and even makes apparent parallel branch updates):
        #   bash
        #   while true; do   printf "\033[H\033[J";   tail -n $(tput lines) out.ansi;   sleep 0.05; done
        # * dump to file as backup for messed up scrollback or just for referencing afterwards (snapshot of past runs)
        with open("out.ansi", "w") as f:
            console = Console(file=f, force_terminal=True, color_system="truecolor", theme=md_theme)
            console.print(root)

    def show_initial_messages(tree: TreeWrapper, live: Live):
        nonlocal input
        if isinstance(input, Command) or input is None:
            # don't show command inputs, that's already obvious in the calling code
            # also avoids clearing screen when resuming after interrupt
            # - currently only use Command to resume after interrupt (could add logic to filter on just resume payload if need to clear with other Command inputs)
            return

        if isinstance(input, dict) and "messages" in input:
            # FYI I am not using this, just added this in case
            # someone passes { "messages": ... } like you would to astream_events
            # don't double wrap in that case
            for msg in input.get("messages", {}):
                increment_message_count()
                _show_message(msg, tree)
            return

        live.console.clear()  # clear screen so thread starts from top of screen
        # FYI only clear when initial messages sent (not on follow up decision in response to an interrupt)
        #   that way you can see full thread still when resuming after interrupts

        initial_messages = input
        # convenience to wrap in messages dict... b/c that's what astream_events/invoke/etc expects
        # I added this so you can pass initial_messages instead of always wrapping it
        input = {"messages": initial_messages}
        for tool_message in initial_messages:
            increment_message_count()
            _show_message(tool_message, tree)

    root = TreeWrapper("agent", hide_root=True)
    root.TREE_GUIDES = [("    ", "    ", "    ", "    ")]

    # Mapping from a runnable's ``run_id`` to the Tree node that represents it.
    # This enables events from parallel runnables to locate the correct parent node and update it
    trees_by_run_id: dict[str, TreeWrapper] = {}

    def register_node(run_id: str, node: TreeWrapper):
        """ explicit method purely for readability """
        trees_by_run_id[run_id] = node
        return node

    with Live(
            root,
            auto_refresh=False,
            # refresh_per_second=8,
            # vertical_overflow="visible",  # FYI overflow is FINE if you don't plan on scrolling back OR you're careful with it
            # FYI for my demos I am fine disabling it as I mostly review after the fact and hence I do not want scrollback mishaps that present a false picture of what happeened
            #   and w/ overflow not visible, this might make editing demos easier...
            #   decide case by case
            #
            # * DISABLE vertical_overflow fixes scrollback problems w/ long traces
            #   auto_refresh=False helped in _SOME_ cases of long traces + vertical overflow, but not all
            #      check w/ HITL and search for top level repeats:
            #        - /thread_id': 'generate_a_thread_id_fawse234awe', 'ls_integration': 'langgraph
            #        - should only find: 1 on_chain_start, 4 on_chain_stream, 1 on_chain_end
            #        - check 6/6 matches, if >6 => messed up
            #   PRN is there a way to dump the live to a file? could I do that at end just to have reliable place to check?
            console=Console(theme=md_theme),
    ) as live:
        show_initial_messages(root, live)

        events: list[StreamEvent] = []
        streaming_state = StreamingChunksState()
        event: StreamEvent
        async for event in agent.astream_events(input, version="v2", config=config, **kwargs):
            # https://reference.langchain.com/python/langchain-core/runnables/base/Runnable/astream_events
            # event type naming: on_[runnable_type]_(start|stream|end)
            # - runnable types: chain, chat_model, tool
            events.append(event)

            if only_dump_events:
                # FYI using root b/c no register/nest logic executes
                dump_all_events_except_streaming_tokens_for_debugging(event, root)
                continue

            # * find parent_node
            # FYI throw if no run_id/parent_ids, I want it to be a show stopper so I can see if/when it ever happens
            run_id: str = event.get("run_id")
            parent_ids: Sequence[str] = event.get("parent_ids")
            if any(parent_ids):
                parent_id = parent_ids[-1]
                parent_node = trees_by_run_id.get(parent_id)
            else:
                parent_node = root

            if parent_node is None:
                root.add_markup("[ERROR: parent node is missing]") \
                    .add_pretty(event) # show event to troubleshoot
                # must abort entirely, not continue... do not catch this error
                raise RuntimeError("Parent node not found, this shouldn't ever happen, aborting...")

            event_type = event["event"]
            try:
                if dump_events:
                    dump_all_events_except_streaming_tokens_for_debugging(event, parent_node)

                if event_type == "on_chain_start":
                    # FYI nesting is somewhat misleading w/ parallel execution => i.e. parallel tool calls... also will be true for other parallel branches
                    #   for now just mention parallelism is limit on this visualizer... and I am NOT ADDING anything for parallelism!
                    #     worst case right now is pretty much just parallel tool calls (and mostly simple ones, not parallel agents though I could trigger that too)
                    #     just mention that would be misleading!
                    #     FYI showing name obviates some of the confusion on the [chain_*] log
                    #   GOOD enough for now IMO... just mention limits of custom visualization (and segue then to LangSmith)!
                    #   PRN track who the parent is (via parent IDs.... build a mapping table of parent ID => node and use last parent ID to get node to attach to...)
                    #    and then don't use a global tree variable?
                    #    rich's live view will work with this... but then you lose timing correlation (where later stuff came later)...
                    #
                    # parent_ids so far are always a chain's run_id (makes sense, each runnable invocation is "wrapped" w/ a chain)
                    #   IOTW nesting is all? organized by chains (b/c every runnable invocation is wrapped in a dedicated chain)
                    #   only leaf nodes contain non-chain events
                    #   BTW on_chain_[start|stream|end] share the same run_id
                    name = event.get("name")
                    chain_start_node = parent_node.add_no_markup(f"[chain start] {name}")  # this label makes it very easy to see in my hierarchy where the chain starts/ends!
                    if run_id is None:
                        raise RuntimeError("Missing run_id for chain start event")
                    register_node(run_id, chain_start_node)
                elif event_type == "on_chain_end":
                    chain_start_node = trees_by_run_id.get(run_id)
                    assert chain_start_node is not None
                    # chain_start_node.add_no_markup(f"[chain end] {event.get("name")}")
                elif event_type == "on_chain_stream":
                    # TODO remove [chain stream] log after done testing lookup
                    chain_start_node = trees_by_run_id.get(run_id)
                    assert chain_start_node is not None
                    # chain_start_node.add_no_markup(f"[chain stream] {event.get("name")}")
                    # PRN show what was streamed? (gonna be a repeat of something nested)
                    #
                    show_approval_interrupts(event, parent_node)
                elif event_type == "on_tool_start":
                    tool_start_node = on_tool_start(event, parent_node)
                    # FYI subagents will result in chain events nested under tool call!
                    #   test with tests/stream_messages/subagents.py  (chain => tool(task) => chain => execute)
                    register_node(run_id, tool_start_node)
                elif event_type == "on_tool_end":
                    # PRN nest tool_end under the tool_start_node?
                    tool_start_node = trees_by_run_id.get(run_id)
                    assert tool_start_node is not None  # start called before end
                    # where to position end node (ToolMessage)?
                    # on_tool_end(event, parent_node) # sibling of tool_start "Calling..." node?
                    on_tool_end(event, tool_start_node)  # or, nested under "Calling..." node?
                elif event_type == "on_chat_model_start":
                    # PRN register_node(run_id, chat_model_start_node)...
                    #    First, register it in on_chat_model_end once fully constructed (streaming)?
                    #      b/c AFAICT nothing else would be nested under the chat_model completion runnable, not AFAICT
                    #        at least not until the completion is done?
                    #        other runnables can trigger as a result but they'd be wrapped in a sibling chain next to this chat_model runnable invocation
                    #      other stuff can happen in parallel but that would be under a diff parent_node
                    #    OR, register it in on_chat_model_stream?
                    #    OR, register it here if something is nested before first on_chat_model_stream call
                    #      would require reworking how streaming updates the node:
                    #      - create node here in _start
                    #      - pass via state
                    #      - modify streaming code to update the same node, not replace (remove/add) like it does now
                    streaming_state.reset()
                elif event_type == "on_chat_model_stream":
                    on_chat_model_stream(event, streaming_state, parent_node)
            except Exception as error:  # pragma: no cover
                # it is largely ok to continue because we are just displaying results
                #  that said, tree hierarchy might be messed up with an exception if parent tree is never created...
                if root:
                    root.add_error("Error processing event", error)
                else:
                    raise RuntimeError("No tree to log error to") from error

            if not event_type.endswith("_stream"):
                # FYI DO NOT try auto_refresh (it is horribly broken)... call refresh here on your own... or just dump it all on exit too
                # move live.rfresh into here to block on _stream for long traces cannot have streaming updates... doesn't make sense anyways... cuz vertical_overflow=False anyways
                #  PRN maybe after message_count > 2 then only update here? that would help make short threads responsive while long ones faster
                live.refresh()  # refresh after each event, works w/ vertical_overflow so far! __knock_on_wood__
                dump_tree_to_out_ansi(root)

    dump_tree_to_out_ansi(root)

    return events

def get_tools(agent: CompiledStateGraph):
    tools_node = agent.nodes["tools"]
    return tools_node.bound.tools_by_name

def show_tools(agent: CompiledStateGraph):
    tools = get_tools(agent)
    rich.inspect(tools)
