"""In-process event bus. Business events feed the workflow engine; live events
feed the dashboard's monitoring websocket."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

EVENT_TYPES = {
    "call.started": "A call was answered by the agent",
    "call.completed": "A call ended and its summary/extraction is available",
    "call.failed": "A call could not be connected (no answer, busy, failed)",
    "call.escalated": "The caller asked for / needed a human",
    "call.transferred": "The call was transferred to a human",
    "contact.opted_out": "A contact asked not to be called again",
    "enquiry.created": "An enquiry record was created",
    "callback.created": "A callback was scheduled",
    "ticket.created": "A support ticket was created",
    "feedback.received": "Caller feedback was recorded",
    "campaign.completed": "An outbound campaign finished",
    "custom": "Custom event posted via the integrations API (e.g. from n8n)",
}

Handler = Callable[[str, dict], Awaitable[Any]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._tasks: set[asyncio.Task] = set()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event_type: str, payload: dict, wait: bool = False) -> None:
        handlers = self._handlers.get(event_type, []) + self._handlers.get("*", [])
        coros = [self._safe(h, event_type, payload) for h in handlers]
        if wait:
            await asyncio.gather(*coros)
        else:
            for c in coros:
                t = asyncio.create_task(c)
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @staticmethod
    async def _safe(h: Handler, event_type: str, payload: dict) -> None:
        try:
            await h(event_type, payload)
        except Exception:
            log.exception("Event handler failed", extra={"ctx": {"event": event_type}})


bus = EventBus()


class LiveHub:
    """Fan-out of live call updates to connected dashboard websockets."""

    def __init__(self) -> None:
        self.calls: dict[str, dict] = {}
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _broadcast(self, msg: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def update(self, call_id: str, **fields) -> None:
        info = self.calls.setdefault(call_id, {"id": call_id, "transcript": []})
        info.update(fields)
        self._broadcast({"type": "call", "call": {k: v for k, v in info.items() if k != "transcript"}})

    def transcript(self, call_id: str, role: str, text: str) -> None:
        info = self.calls.get(call_id)
        if info is None:
            return
        item = {"role": role, "text": text}
        info["transcript"] = (info["transcript"] + [item])[-30:]
        self._broadcast({"type": "transcript", "call_id": call_id, **item})

    def remove(self, call_id: str) -> None:
        self.calls.pop(call_id, None)
        self._broadcast({"type": "ended", "call_id": call_id})

    def snapshot(self) -> list[dict]:
        return list(self.calls.values())


live = LiveHub()
