"""WebSocket endpoints.

/ws/voice      browser test console / simulated inbound phone call
/ws/simphone   simulated handset that receives outbound calls
/ws/live       live call monitoring feed for the dashboard

Audio protocol: binary frames are 16-bit LE mono PCM (client->server 16 kHz,
server->client 24 kHz). Text frames are JSON control/events."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import SessionLocal
from ..security import has_role, ws_user
from ..services.events import live
from ..telephony.base import capacity, get_provider
from ..telephony.numbers import InvalidNumber, normalise
from ..telephony.sessions import meta_for_inbound, meta_for_outbound, run_session
from ..telephony.simulator import SimHandset, SimulatorProvider
from ..voice.pipeline import Transport

log = logging.getLogger(__name__)
router = APIRouter()


class WebSocketTransport(Transport):
    channel = "browser"
    in_rate = 16000
    out_rate = 24000
    playback_lead_s = 0.25

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.session = None
        self.closed = False

    async def send_audio(self, pcm16: bytes) -> None:
        if not self.closed:
            try:
                await self.ws.send_bytes(pcm16)
            except Exception:
                self.closed = True

    async def clear_audio(self) -> None:
        await self.send_event({"type": "clear"})

    async def send_event(self, event: dict) -> None:
        if not self.closed:
            try:
                await self.ws.send_text(json.dumps(event, default=str))
            except Exception:
                self.closed = True

    async def hangup(self, reason: str) -> None:
        await self.send_event({"type": "hangup", "reason": reason})


async def _auth(ws: WebSocket, role: str):
    async with SessionLocal() as db:
        user = await ws_user(ws, db)
    if not user or not has_role(user, role):
        await ws.close(code=4401)
        return None
    return user


async def _pump(ws: WebSocket, transport: WebSocketTransport, session_task: asyncio.Task, on_text=None) -> None:
    """Forward client frames to the session until either side ends."""
    recv: asyncio.Task | None = None
    try:
        while not session_task.done():
            recv = asyncio.create_task(ws.receive())
            done, _ = await asyncio.wait({recv, session_task}, return_when=asyncio.FIRST_COMPLETED)
            if session_task in done:
                break
            msg = recv.result()
            recv = None
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None and transport.session:
                transport.session.feed_audio(msg["bytes"])
            elif msg.get("text"):
                data = json.loads(msg["text"])
                if on_text and await on_text(data):
                    continue
                if not transport.session:
                    continue
                if data.get("type") == "text" and data.get("text"):
                    transport.session.submit_text(str(data["text"])[:2000])
                elif data.get("type") == "hangup":
                    transport.session.request_end("completed", "caller_hangup")
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if recv and not recv.done():
            recv.cancel()
        transport.closed = True
        if transport.session:
            transport.session.request_end("completed", "caller_hangup")
        await asyncio.gather(session_task, return_exceptions=True)


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket, mode: str = "test", caller: str = "", did: str = ""):
    await ws.accept()
    user = await _auth(ws, "operator")
    if not user:
        return
    if not await capacity().try_acquire():
        await ws.send_text(json.dumps({"type": "error", "message": "All call channels are busy. Try again shortly."}))
        await ws.close(code=4429)
        return
    try:
        number = ""
        if caller:
            try:
                number = normalise(caller)
            except InvalidNumber as e:
                await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
                await ws.close()
                return
        transport = WebSocketTransport(ws)
        is_test = mode != "simulate"
        transport.channel = "browser" if is_test else "simulator"
        meta = await meta_for_inbound(transport.channel, number or "browser-test", did or "test-console", is_test=is_test)
        await ws.send_text(json.dumps({"type": "ready", "in_rate": transport.in_rate, "out_rate": transport.out_rate}))
        task = asyncio.create_task(run_session(transport, meta))
        await asyncio.sleep(0)
        await _pump(ws, transport, task)
    finally:
        await capacity().release()
        with contextlib.suppress(Exception):
            await ws.close()


@router.websocket("/ws/simphone")
async def ws_simphone(ws: WebSocket, number: str):
    """A browser tab acting as a phone on the simulated network."""
    await ws.accept()
    if not await _auth(ws, "operator"):
        return
    provider = get_provider()
    if not isinstance(provider, SimulatorProvider):
        await ws.send_text(json.dumps({"type": "error", "message": "Simulated handsets need TELEPHONY_PROVIDER=simulator"}))
        await ws.close()
        return
    try:
        number = normalise(number)
    except InvalidNumber as e:
        await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        await ws.close()
        return

    ring_future: asyncio.Future | None = None
    transport = WebSocketTransport(ws)
    transport.channel = "simulator"
    in_call = asyncio.Event()
    call_done = asyncio.Event()

    async def ring(call_id: str, req) -> str:
        nonlocal ring_future
        if in_call.is_set():
            return "busy"
        ring_future = asyncio.get_running_loop().create_future()
        await transport.send_event({"type": "incoming", "call_id": call_id, "from": req.caller_id or "BongoHive"})
        try:
            return await asyncio.wait_for(asyncio.shield(ring_future), timeout=req.timeout_s - 1)
        except asyncio.TimeoutError:
            await transport.send_event({"type": "missed", "call_id": call_id})
            return "no_answer"

    async def run_call(call_id: str) -> None:
        in_call.set()
        call_done.clear()
        try:
            meta = await meta_for_outbound(call_id, "simulator")
            await transport.send_event({"type": "ready", "in_rate": transport.in_rate, "out_rate": transport.out_rate})
            await run_session(transport, meta)
        finally:
            transport.session = None
            in_call.clear()
            call_done.set()

    handset = SimHandset(number, ring, run_call)
    provider.register(handset)
    await transport.send_event({"type": "registered", "number": number})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if transport.session:
                    transport.session.feed_audio(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            t = data.get("type")
            if t in ("answer", "decline") and ring_future and not ring_future.done():
                ring_future.set_result("answer" if t == "answer" else "busy")
            elif t == "hangup" and transport.session:
                transport.session.request_end("completed", "caller_hangup")
            elif t == "text" and transport.session:
                transport.session.submit_text(str(data.get("text", ""))[:2000])
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        provider.unregister(number, handset)
        transport.closed = True
        if ring_future and not ring_future.done():
            ring_future.set_result("no_answer")
        if transport.session:
            transport.session.request_end("completed", "caller_hangup")


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    if not await _auth(ws, "operator"):
        return
    q = live.subscribe()
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "calls": live.snapshot()}, default=str))
        while True:
            getter = asyncio.create_task(q.get())
            recv = asyncio.create_task(ws.receive())
            done, pending = await asyncio.wait({getter, recv}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            if recv in done:
                if recv.result()["type"] == "websocket.disconnect":
                    break
                continue
            await ws.send_text(json.dumps(getter.result(), default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        live.unsubscribe(q)
