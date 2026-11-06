"""Local telephone network simulation.

Inbound: the dashboard's test console "calls" the agent over a websocket.
Outbound: the dialer rings a simulated handset (a browser tab registered for a
number on /ws/simphone). The handset can answer, decline (busy) or ignore
(no answer). Numbers with no handset get a configurable default outcome, and
tests can register scripted handsets."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .base import OriginateRequest, TelephonyProvider, capacity

log = logging.getLogger(__name__)

# behaviour for numbers with no simulated handset attached
DEFAULT_OUTCOME = "no_answer"


class SimHandset:
    """A registered handset. `ring` returns 'answer' | 'busy' | 'no_answer'."""

    def __init__(self, number: str, ring: Callable[[str, OriginateRequest], Awaitable[str]],
                 run_call: Callable[[str], Awaitable[None]]) -> None:
        self.number = number
        self.ring = ring
        self.run_call = run_call


class SimulatorProvider(TelephonyProvider):
    name = "simulator"

    def __init__(self) -> None:
        super().__init__()
        self.handsets: dict[str, SimHandset] = {}
        self.default_outcome = DEFAULT_OUTCOME
        self.dialled: list[OriginateRequest] = []
        self._tasks: set[asyncio.Task] = set()

    def register(self, handset: SimHandset) -> None:
        self.handsets[handset.number] = handset

    def unregister(self, number: str, handset: SimHandset | None = None) -> None:
        if handset is None or self.handsets.get(number) is handset:
            self.handsets.pop(number, None)

    async def originate(self, req: OriginateRequest) -> None:
        self.dialled.append(req)
        t = asyncio.create_task(self._dial(req))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _dial(self, req: OriginateRequest) -> None:
        from ..services.calls import update_call

        await update_call(req.call_id, status="ringing")
        hs = self.handsets.get(req.to_number)
        if hs is None:
            await asyncio.sleep(0.2)
            await self.report_outcome(req.call_id, self.default_outcome, "simulated: no handset registered")
            return
        try:
            result = await asyncio.wait_for(hs.ring(req.call_id, req), timeout=req.timeout_s)
        except asyncio.TimeoutError:
            result = "no_answer"
        if result != "answer":
            await self.report_outcome(req.call_id, result, f"simulated: {result}")
            return
        if not await capacity().try_acquire():
            await self.report_outcome(req.call_id, "failed", "capacity: max concurrent calls reached")
            return
        try:
            await hs.run_call(req.call_id)
        finally:
            await capacity().release()

    async def health(self) -> dict:
        return {"ok": True, "provider": "simulator", "detail": f"{len(self.handsets)} simulated handset(s) online",
                "handsets": list(self.handsets)}
