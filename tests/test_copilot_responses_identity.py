"""Copilot's changing SSE IDs must not create duplicate client messages."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

from router_maestro.protocols import WireProtocol
from router_maestro.providers.copilot import CopilotProvider
from router_maestro.routing.model_ref import ModelRef


def _message(item_id: str, text: str = "你好") -> dict:
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
        "future": {"preserved": True},
    }


def _frames(prefix: str) -> list[dict]:
    return [
        {"type": "response.created", "response": {"id": f"{prefix}-created", "output": []}},
        {"type": "response.in_progress", "response": {"id": f"{prefix}-progress"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**_message(f"{prefix}-added", ""), "status": "in_progress"},
        },
        {
            "type": "response.content_part.added",
            "output_index": 0,
            "content_index": 0,
            "item_id": f"{prefix}-part-added",
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
        {
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": f"{prefix}-delta",
            "delta": "你好",
        },
        {
            "type": "response.output_text.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": f"{prefix}-text-done",
            "text": "你好",
        },
        {
            "type": "response.content_part.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": f"{prefix}-part-done",
            "part": _message("unused")["content"][0],
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": _message(f"{prefix}-done"),
        },
        {
            "type": "response.completed",
            "response": {
                "id": f"{prefix}-completed",
                "status": "completed",
                "output": [_message(f"{prefix}-snapshot")],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        },
    ]


class _SSEStream(httpx.AsyncByteStream):
    def __init__(self, frames: list[dict]) -> None:
        self.frames = frames
        self.closed = False

    async def __aiter__(self):
        for frame in self.frames:
            await asyncio.sleep(0)
            yield f"data: {json.dumps(frame)}\n\n".encode()

    async def aclose(self) -> None:
        self.closed = True


async def _collect_streams(*streams: list[dict]) -> list[list[dict]]:
    upstreams = [_SSEStream(frames) for frames in streams]

    def handler(request: httpx.Request) -> httpx.Response:
        index = int(json.loads(request.content)["input"])
        return httpx.Response(200, stream=upstreams[index])

    provider = CopilotProvider()
    provider._cached_token = "token"
    provider.ensure_token = AsyncMock()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    binding = next(b for b in provider.bindings() if b.protocol is WireProtocol.OPENAI_RESPONSES)

    async def collect(index: int) -> list[dict]:
        attempt = await binding.prepare_attempt(
            model=ModelRef(provider=provider.name, upstream_id="gpt-6-astra"),
            payload={"input": str(index)},
            stream=True,
        )
        return [dict(frame) async for frame in binding.executor.execute_stream(attempt)]

    try:
        results = await asyncio.gather(*(collect(i) for i in range(len(streams))))
    finally:
        await provider.close()
    assert all(upstream.closed for upstream in upstreams)
    return results


@pytest.mark.asyncio
async def test_streamed_message_keeps_one_id_through_completion_and_concurrent_requests():
    streams = [_frames("first"), _frames("second")]
    results = await _collect_streams(*streams)
    for original, frames in zip(streams, results, strict=True):
        expected = deepcopy(original)
        response_id = original[0]["response"]["id"]
        message_id = original[2]["item"]["id"]
        for frame in expected:
            if "response" in frame:
                frame["response"]["id"] = response_id
                for item in frame["response"].get("output", []):
                    item["id"] = message_id
            if "item" in frame:
                frame["item"]["id"] = message_id
            if "item_id" in frame:
                frame["item_id"] = message_id
        assert frames == expected


@pytest.mark.asyncio
async def test_identical_messages_keep_separate_ids_and_opaque_items_keep_upstream_ids():
    reasoning = {"type": "reasoning", "id": "rs-signed", "encrypted_content": "signed-blob"}
    tool = {
        "type": "function_call",
        "id": "fc-done",
        "call_id": "call-original",
        "name": "lookup",
        "namespace": "functions",
        "arguments": "{}",
    }
    frames = [
        {"type": "response.output_item.added", "output_index": 0, "item": _message("msg-a")},
        {"type": "response.output_item.done", "output_index": 0, "item": _message("msg-b")},
        {"type": "response.output_item.added", "output_index": 1, "item": _message("msg-c")},
        {"type": "response.output_item.done", "output_index": 1, "item": _message("msg-d")},
        {"type": "response.output_item.done", "output_index": 2, "item": reasoning},
        {"type": "response.output_item.done", "output_index": 3, "item": tool},
        {
            "type": "response.completed",
            "response": {
                "id": "resp-final",
                "output": [
                    _message("msg-e"),
                    _message("msg-f"),
                    reasoning,
                    tool,
                ],
            },
        },
    ]
    [result] = await _collect_streams(frames)
    output = result[-1]["response"]["output"]
    assert [item["id"] for item in output] == ["msg-a", "msg-c", "rs-signed", "fc-done"]
    assert result[1]["item"]["id"] == "msg-a"
    assert result[3]["item"]["id"] == "msg-c"
    assert result[4:6] == frames[4:6]
    assert output[2:] == [reasoning, tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["output_text", "refusal"])
async def test_delta_without_item_added_establishes_message_identity(kind: str):
    item = _message("msg-done")
    if kind == "refusal":
        item["content"] = [{"type": "refusal", "refusal": "no"}]
    frames = [
        {
            "type": f"response.{kind}.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg-delta",
            "delta": "你好" if kind == "output_text" else "no",
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
    ]
    [result] = await _collect_streams(frames)
    assert result[0] == frames[0]
    assert result[1]["item"]["id"] == "msg-delta"


@pytest.mark.asyncio
async def test_stable_ids_and_unknown_extensions_are_preserved():
    frames = _frames("stable")
    for frame in frames:
        if "response" in frame:
            frame["response"]["id"] = "resp-stable"
            for item in frame["response"].get("output", []):
                item["id"] = "msg-stable"
        if "item" in frame:
            frame["item"]["id"] = "msg-stable"
        if "item_id" in frame:
            frame["item_id"] = "msg-stable"
        frame["future_event_field"] = {"preserved": True}
    [result] = await _collect_streams(frames)
    assert result == frames
