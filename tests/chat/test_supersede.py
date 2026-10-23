"""A newer message in a conversation replaces the turn that is still running."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bike_routing_agent.chat.service import ChatService, TurnSuperseded

from .test_chat_graph import FakePlanner, Talk, ready_plan


class SlowPlanner(FakePlanner):
    """The first plan waits (and notices being cancelled); later ones answer at once."""

    def __init__(self) -> None:
        super().__init__()
        self.cancelled = asyncio.Event()
        self.waiting = asyncio.Event()
        self.slow_calls = 1

    async def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if self.slow_calls:
            self.slow_calls -= 1
            self.waiting.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return ready_plan()


async def test_a_newer_message_replaces_the_running_turn():
    planner = SlowPlanner()
    service = ChatService(planner)
    talk = Talk(service)
    planner.slow_calls = 0
    await talk.say("from Braunschweig to Goslar")  # opens the conversation
    planner.slow_calls = 1

    first = asyncio.ensure_future(talk.say("from Hannover to Celle"))
    await asyncio.wait_for(planner.waiting.wait(), timeout=5)
    second = await asyncio.wait_for(talk.say("from Hannover to Wolfsburg"), timeout=5)

    with pytest.raises(TurnSuperseded):
        await first
    assert planner.cancelled.is_set()  # the abandoned plan was stopped, not left running
    assert "Wolfsburg" in planner.requests[-1]["destination"]
    assert second.plan is not None and not second.failed


async def test_the_turn_after_a_replaced_one_plans_normally():
    """A cancelled turn leaves the conversation usable: the next message is a fresh request, not
    the answer to a question that was never asked."""
    planner = SlowPlanner()
    service = ChatService(planner)
    talk = Talk(service)
    planner.slow_calls = 0
    await talk.say("from Braunschweig to Goslar")
    planner.slow_calls = 1
    first = asyncio.ensure_future(talk.say("from Hannover to Celle"))
    await asyncio.wait_for(planner.waiting.wait(), timeout=5)
    cancelled_turn = asyncio.ensure_future(talk.say("from Hannover to Wolfsburg"))
    with pytest.raises(TurnSuperseded):
        await first
    await cancelled_turn
    after = await talk.say("from Celle to Goslar")
    assert after.plan is not None and not after.failed
    assert planner.requests[-1]["origin"] == "Celle"


async def test_a_caller_that_goes_away_stops_its_turn():
    planner = SlowPlanner()
    service = ChatService(planner)
    turn = asyncio.ensure_future(service.send("from A to B"))
    await asyncio.wait_for(planner.waiting.wait(), timeout=5)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    await asyncio.sleep(0.05)
    assert planner.cancelled.is_set()
