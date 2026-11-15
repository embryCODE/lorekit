"""GM replies must never vanish: narration passed only to turn_save is still shown."""

import json

import pytest

from lorekit.orchestrator import _transform_events
from lorekit.providers.base import GameEvent, StreamChunk
from lorekit.providers.claude.parse import parse_jsonl_line


async def _collect(chunks):
    async def gen():
        for c in chunks:
            yield c

    return [e async for e in _transform_events(gen())]


@pytest.mark.asyncio
async def test_narration_only_passed_to_turn_save_is_shown():
    events = await _collect(
        [
            StreamChunk(type="saved_narration", content="The road winds on."),
            StreamChunk(type="text", content="(Saved.)"),
        ]
    )
    assert events == [GameEvent(type="narration", content="The road winds on.\n\n(Saved.)")]


@pytest.mark.asyncio
async def test_narration_already_shown_is_not_duplicated():
    events = await _collect(
        [
            StreamChunk(type="text", content="The road winds on."),
            StreamChunk(type="saved_narration", content="The road  winds on."),
        ]
    )
    assert events == [GameEvent(type="narration", content="The road winds on.")]


@pytest.mark.asyncio
async def test_falls_back_to_result_text():
    events = await _collect([StreamChunk(type="result_text", content="Hello there.")])
    assert events == [GameEvent(type="narration", content="Hello there.")]


@pytest.mark.asyncio
async def test_empty_turn_is_an_error_not_silence():
    events = await _collect([StreamChunk(type="tool_use", content="turn_save")])
    assert [e.type for e in events] == ["error"]


def test_parse_assistant_turn_save_narration():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__lorekit__turn_save", "input": {"narration": "A scene."}}
                ]
            },
        }
    )
    chunk = parse_jsonl_line(line)
    assert chunk.type == "saved_narration"
    assert chunk.content == "A scene."


def test_parse_result_text():
    chunk = parse_jsonl_line(json.dumps({"type": "result", "result": "Done."}))
    assert chunk.type == "result_text"
    assert chunk.content == "Done."
