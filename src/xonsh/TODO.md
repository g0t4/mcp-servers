# TODO

## Persistent session / shell state

Right now each `run_xonsh` call is a fresh process with no persistent state.
Isolation is a feature (deterministic, no leaked env), but there could be value
in a "keep session alive" mode for performance or for long-running workflows.
Consider a `session` parameter that reuses a persistent xonsh process across
calls, or a way to opt into shell state preservation.

## Client-side file writing

Write `run_xonsh` results (stdout, stderr, metadata) to a file for long-term
persistence and cross-turn reference. This would let agents:

- Keep a record of what ran, where, and what happened
- Reference outputs later without re-running
- Chain operations across multiple tool calls
- Debug by looking back at past executions

Consider a run log file with timestamp, cwd, exit code, truncated output, etc.
Or structured output with session-based organization (e.g. `~/.xonsh_mcp/sessions/<timestamp>/`).
