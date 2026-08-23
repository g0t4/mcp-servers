"""Tests for client-side request cancellation in the subagents MCP server."""

import anyio
import pytest
from unittest.mock import patch

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CancelledNotification, CancelledNotificationParams

from subagents.server import create_server


@pytest.mark.anyio
async def test_client_cancel_aborts_count_tool():
    """A client's notifications/cancelled aborts an in-flight count request."""
    server = create_server()

    started = anyio.Event()
    cancelled = anyio.Event()
    errors = []

    async def hanging_sleep(*args, **kwargs):
        started.set()
        try:
            await anyio.sleep(60)
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    async with create_connected_server_and_client_session(server) as client:
        with patch("subagents.count.asyncio.sleep", hanging_sleep):
            # The request id the next call_tool will use.
            request_id = client._request_id

            async def call_tool_expect_cancel():
                try:
                    await client.call_tool(
                        "count", {"to": 10}, meta={"progressToken": "p"}
                    )
                except Exception as e:
                    errors.append(e)

            async with anyio.create_task_group() as tg:
                tg.start_soon(call_tool_expect_cancel)
                await started.wait()
                await client.send_notification(
                    CancelledNotification(
                        params=CancelledNotificationParams(requestId=request_id)
                    )
                )
                with anyio.fail_after(5):
                    await cancelled.wait()
                # Give the client task a moment to receive the cancel error.
                await anyio.sleep(0.2)

    assert cancelled.is_set()
    assert [str(e) for e in errors] == ["Request cancelled"]
