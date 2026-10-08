"""Shared logging setup for MCP servers.

Writes rich-formatted logs and trace files to an XDG-compliant state dir:

    $XDG_STATE_HOME/mcp-servers/<name>.log
    $XDG_STATE_HOME/mcp-servers/traces/

Falls back to ``~/.local/state/mcp-servers/`` when ``XDG_STATE_HOME`` is unset.

Also routes stdlib ``logging`` (which FastMCP uses internally) to the same log
file so framework logs don't pollute stderr.

This module is intentionally self-contained so it can be copied verbatim into
other MCP server packages (e.g. ``mcp_server_xonsh``) to keep logging logic in
one place.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from rich.console import Console


class LogContext:
    """Holds the rich console and trace directory for one MCP server.

    Attributes:
        name: Prefix for the log file (e.g. ``"agent"`` -> ``agent.log``).
        log_dir: Directory containing the log file.
        traces_dir: Directory for per-run trace files.
        log_file: Open file handle backing ``console``.
        console: Rich console writing to ``log_file``.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.log_dir = self._state_dir()
        self.traces_dir = self.log_dir / "traces"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = open(self.log_dir / f"{name}.log", "a")
        self.console = Console(file=self.log_file, force_terminal=True)

    def configure_python_logging(self, level: int = logging.INFO) -> None:
        """Route stdlib ``logging`` (FastMCP internals) to this log file.

        FastMCP's ``configure_logging`` calls ``logging.basicConfig`` with a
        handler bound to ``Console(stderr=True)``. ``basicConfig`` is a no-op
        once the root logger already has handlers, so pre-installing a
        file-backed handler here keeps MCP framework logs off stderr and on
        disk instead.
        """
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        from rich.logging import RichHandler

        handler = RichHandler(console=self.console, rich_tracebacks=True)
        root.addHandler(handler)
        root.setLevel(level)

    @staticmethod
    def _state_dir() -> Path:
        xdg_state = os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
        )
        return Path(xdg_state) / "mcp-servers"
