import asyncio

import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
async def test_message_tool_forwards_thread_metadata() -> None:
    sent: list[OutboundMessage] = []

    async def _capture(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_capture, default_channel="telegram", default_chat_id="-100123")
    result = await tool.execute(content="test", message_thread_id=42)

    assert result == "Message sent to telegram:-100123"
    assert len(sent) == 1
    assert sent[0].metadata["message_thread_id"] == 42


@pytest.mark.asyncio
async def test_message_tool_context_is_isolated_per_task() -> None:
    sent: list[OutboundMessage] = []

    async def _capture(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_capture)

    async def _worker(chat_id: str, thread_id: int, delay: float) -> None:
        tool.set_context("telegram", chat_id, message_id=10, message_thread_id=thread_id)
        await asyncio.sleep(delay)
        await tool.execute(content=f"hello-{chat_id}")

    await asyncio.gather(
        _worker("chat-a", 101, 0.05),
        _worker("chat-b", 202, 0.0),
    )

    assert len(sent) == 2
    by_chat = {msg.chat_id: msg for msg in sent}
    assert by_chat["chat-a"].metadata["message_thread_id"] == 101
    assert by_chat["chat-b"].metadata["message_thread_id"] == 202
