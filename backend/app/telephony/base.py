"""Telephony provider interface. A provider connects real (or simulated) phone
calls to CallSession and reports outbound dial outcomes."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Dial outcomes reported for outbound attempts that never reached the agent
DIAL_OUTCOMES = ("no_answer", "busy", "failed", "rejected", "voicemail")


@dataclass
class OriginateRequest:
    call_id: str
    to_number: str
    caller_id: str
    timeout_s: int = 30


OutcomeCallback = Callable[[str, str, str], Awaitable[None]]  # (call_id, outcome, cause)


class TelephonyProvider(ABC):
    name = "base"

    def __init__(self) -> None:
        self.on_dial_outcome: OutcomeCallback | None = None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    @abstractmethod
    async def originate(self, req: OriginateRequest) -> None:
        """Start dialling. When answered the provider must run a CallSession for
        req.call_id; otherwise it must call on_dial_outcome."""

    @abstractmethod
    async def health(self) -> dict: ...

    async def report_outcome(self, call_id: str, outcome: str, cause: str = "") -> None:
        if self.on_dial_outcome:
            try:
                await self.on_dial_outcome(call_id, outcome, cause)
            except Exception:
                log.exception("Dial outcome handler failed")


class CallCapacity:
    """Global limit on concurrent agent sessions (CPU/GPU bound)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self.active >= self.limit:
                return False
            self.active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self.active = max(0, self.active - 1)

    @property
    def available(self) -> int:
        return max(0, self.limit - self.active)


_capacity: CallCapacity | None = None


def capacity() -> CallCapacity:
    global _capacity
    if _capacity is None:
        from ..config import get_settings

        _capacity = CallCapacity(get_settings().max_concurrent_calls)
    return _capacity


_provider: TelephonyProvider | None = None


def get_provider() -> TelephonyProvider:
    global _provider
    if _provider is None:
        from ..config import get_settings

        kind = get_settings().telephony_provider
        if kind == "asterisk":
            from .asterisk import AsteriskProvider

            _provider = AsteriskProvider()
        else:
            from .simulator import SimulatorProvider

            _provider = SimulatorProvider()
    return _provider


def set_provider(p: TelephonyProvider | None) -> None:
    global _provider
    _provider = p
