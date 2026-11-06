"""Asterisk integration (SIP trunk -> Asterisk -> this app).

* Media: Asterisk's AudioSocket application streams 8 kHz signed-linear audio
  over TCP to our AudioSocket server, and plays back what we send.
* Control: the Asterisk Manager Interface (AMI) originates outbound calls,
  reports dial outcomes (busy / no answer / congestion) and redirects channels
  for human transfer.
* Call metadata: the dialplan calls `CURL()` on /api/telephony/asterisk/register
  before AudioSocket, so we know the caller ID, DID and channel name, and can
  refuse calls when at capacity.

See telephony/asterisk/*.conf for the matching dialplan and docs/TELEPHONY_ZAMBIA.md."""
from __future__ import annotations

import asyncio
import logging
import struct
import time
import uuid
from dataclasses import dataclass, field

from ..config import get_settings
from ..voice.pipeline import Transport
from .base import OriginateRequest, TelephonyProvider, capacity
from .numbers import normalise

log = logging.getLogger(__name__)

KIND_HANGUP = 0x00
KIND_UUID = 0x01
KIND_DTMF = 0x03
KIND_AUDIO = 0x10
KIND_ERROR = 0xFF

# AMI OriginateResponse "Reason" codes -> our outcomes
_ORIGINATE_REASONS = {"0": "failed", "1": "rejected", "3": "no_answer", "5": "busy", "8": "failed"}


@dataclass
class PendingCall:
    uuid: str
    direction: str
    from_number: str
    to_number: str
    channel: str
    call_id: str | None = None
    amd_status: str = ""
    created: float = field(default_factory=time.monotonic)
    capacity_reserved: bool = False


class AudioSocketTransport(Transport):
    channel = "asterisk"
    in_rate = 8000
    out_rate = 8000
    playback_lead_s = 0.06
    supports_transfer = True

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, provider: AsteriskProvider,
                 pending: PendingCall) -> None:
        self.reader = reader
        self.writer = writer
        self.provider = provider
        self.pending = pending
        self.session = None
        self.closed = False
        self.dtmf: list[str] = []

    async def send_audio(self, pcm16: bytes) -> None:
        if self.closed:
            return
        # 8 kHz frames of 320 bytes (20 ms)
        for i in range(0, len(pcm16), 320):
            part = pcm16[i:i + 320]
            self.writer.write(struct.pack(">BH", KIND_AUDIO, len(part)) + part)
        try:
            await self.writer.drain()
        except ConnectionError:
            self.closed = True

    async def hangup(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.writer.write(struct.pack(">BH", KIND_HANGUP, 0))
            await self.writer.drain()
            self.writer.close()
        except Exception:
            pass

    async def transfer(self, target: str) -> bool:
        return await self.provider.redirect_to_human(self.pending.channel, target)


class AMIClient:
    """Minimal asyncio Asterisk Manager Interface client with auto-reconnect."""

    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self.host, self.port, self.user, self.password = host, port, user, password
        self.connected = False
        self.last_error = ""
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        self.event_handlers: list = []

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._writer:
            self._writer.close()

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
                self._writer = writer
                await reader.readline()  # banner
                login = asyncio.create_task(self.action("Login", Username=self.user, Secret=self.password, Events="call,system"))
                reading = asyncio.create_task(self._read_loop(reader))
                resp = await asyncio.wait_for(login, 5)
                if resp.get("Response") != "Success":
                    raise ConnectionError(f"AMI login failed: {resp.get('Message')}")
                self.connected = True
                self.last_error = ""
                backoff = 1
                log.info("AMI connected", extra={"ctx": {"host": self.host}})
                await reading
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("AMI connection problem", extra={"ctx": {"error": self.last_error, "retry_s": backoff}})
            self.connected = False
            for f in self._pending.values():
                if not f.done():
                    f.set_exception(ConnectionError("AMI disconnected"))
            self._pending.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        msg: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line:
                raise ConnectionError("AMI closed connection")
            line = line.decode(errors="replace").rstrip("\r\n")
            if line:
                if ":" in line:
                    k, v = line.split(":", 1)
                    msg[k.strip()] = v.strip()
                continue
            if not msg:
                continue
            aid = msg.get("ActionID")
            if "Response" in msg and aid in self._pending:
                fut = self._pending.pop(aid)
                if not fut.done():
                    fut.set_result(msg)
            elif "Event" in msg:
                for h in self.event_handlers:
                    try:
                        await h(msg)
                    except Exception:
                        log.exception("AMI event handler failed")
            msg = {}

    async def action(self, name: str, action_id: str | None = None, **fields) -> dict:
        if not self._writer:
            raise ConnectionError("AMI not connected")
        aid = action_id or uuid.uuid4().hex
        lines = [f"Action: {name}", f"ActionID: {aid}"]
        for k, v in fields.items():
            if isinstance(v, list):
                lines += [f"{k}: {x}" for x in v]
            else:
                lines.append(f"{k}: {v}")
        fut = asyncio.get_running_loop().create_future()
        self._pending[aid] = fut
        self._writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await self._writer.drain()
        return await asyncio.wait_for(fut, 10)


class AsteriskProvider(TelephonyProvider):
    name = "asterisk"

    def __init__(self) -> None:
        super().__init__()
        s = get_settings()
        self.s = s
        self.pending: dict[str, PendingCall] = {}
        self.by_call_id: dict[str, str] = {}
        self.ami = AMIClient(s.asterisk_ami_host, s.asterisk_ami_port, s.asterisk_ami_user,
                             s.asterisk_ami_password.get_secret_value())
        self.ami.event_handlers.append(self._on_ami_event)
        self._server: asyncio.base_events.Server | None = None
        self.sessions: dict[str, AudioSocketTransport] = {}

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_audiosocket, self.s.audiosocket_host, self.s.audiosocket_port)
        log.info("AudioSocket server listening", extra={"ctx": {"port": self.s.audiosocket_port}})
        if self.s.asterisk_ami_password.get_secret_value():
            self.ami.start()
        else:
            log.warning("ASTERISK_AMI_PASSWORD not set: outbound calls and transfers are disabled")
        asyncio.create_task(self._gc_pending())

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        await self.ami.stop()

    async def health(self) -> dict:
        ok = self._server is not None and self.ami.connected
        return {"ok": ok, "provider": "asterisk", "audiosocket": self._server is not None,
                "ami_connected": self.ami.connected, "detail": self.ami.last_error or ("connected" if ok else "AMI not connected"),
                "active_sessions": len(self.sessions)}

    async def _gc_pending(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            for k, p in list(self.pending.items()):
                if now - p.created > 120:
                    self.pending.pop(k, None)
                    if p.capacity_reserved:
                        await capacity().release()

    # ------------------------------------------------------------- dialplan hook
    async def register_call(self, direction: str, from_number: str, to_number: str, channel: str,
                            va_uuid: str = "", amd_status: str = "") -> str:
        """Called from the dialplan (via HTTP) right before AudioSocket. Returns
        the UUID to use, 'BUSY' when at capacity, or 'HANGUP' to drop the call."""
        if direction == "outbound":
            p = self.pending.get(va_uuid)
            if not p:
                return "HANGUP"
            p.channel = channel or p.channel
            p.amd_status = amd_status
            if amd_status == "MACHINE":
                from .sessions import meta_for_outbound

                meta = await meta_for_outbound(p.call_id, "asterisk")
                if meta.voicemail_action != "leave_message":
                    self.pending.pop(va_uuid, None)
                    await self.report_outcome(p.call_id, "voicemail", "AMD: answering machine")
                    return "HANGUP"
            return va_uuid
        if not await capacity().try_acquire():
            log.warning("Inbound call rejected: at capacity", extra={"ctx": {"from_suffix": from_number[-3:]}})
            return "BUSY"
        u = str(uuid.uuid4())
        try:
            from_e164 = normalise(from_number)
        except Exception:
            from_e164 = from_number
        self.pending[u] = PendingCall(u, "inbound", from_e164, to_number, channel, capacity_reserved=True)
        return u

    # ------------------------------------------------------------- media
    async def _on_audiosocket(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pending: PendingCall | None = None
        transport: AudioSocketTransport | None = None
        task: asyncio.Task | None = None
        try:
            kind, payload = await self._read_msg(reader)
            if kind != KIND_UUID or len(payload) != 16:
                raise ConnectionError("expected UUID message first")
            u = str(uuid.UUID(bytes=payload))
            pending = self.pending.pop(u, None)
            if pending is None:
                log.warning("AudioSocket connection with unknown UUID", extra={"ctx": {"uuid": u}})
                writer.write(struct.pack(">BH", KIND_HANGUP, 0))
                await writer.drain()
                return
            transport = AudioSocketTransport(reader, writer, self, pending)
            self.sessions[u] = transport
            from .sessions import meta_for_inbound, meta_for_outbound, run_session

            if pending.direction == "inbound":
                meta = await meta_for_inbound("asterisk", pending.from_number, pending.to_number, provider_call_id=pending.channel)
            else:
                meta = await meta_for_outbound(pending.call_id, "asterisk")
                if pending.amd_status == "MACHINE":
                    meta.opening_line = meta.voicemail_message or meta.opening_line
            task = asyncio.create_task(run_session(transport, meta))
            await asyncio.sleep(0)  # let the session attach itself to the transport
            while True:
                kind, payload = await self._read_msg(reader)
                if kind == KIND_AUDIO and transport.session:
                    transport.session.feed_audio(payload)
                elif kind == KIND_DTMF:
                    transport.dtmf.append(payload.decode(errors="ignore"))
                elif kind in (KIND_HANGUP, KIND_ERROR):
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:
            log.exception("AudioSocket handler error")
        finally:
            if transport:
                transport.closed = True
                if transport.session:
                    transport.session.request_end("completed", "caller_hangup")
            if task:
                await asyncio.gather(task, return_exceptions=True)
            if pending:
                self.sessions.pop(pending.uuid, None)
                if pending.capacity_reserved:
                    await capacity().release()
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    async def _read_msg(reader: asyncio.StreamReader) -> tuple[int, bytes]:
        hdr = await reader.readexactly(3)
        kind, length = struct.unpack(">BH", hdr)
        payload = await reader.readexactly(length) if length else b""
        return kind, payload

    # ------------------------------------------------------------- outbound
    def dial_string(self, e164: str) -> str:
        fmt = self.s.asterisk_dial_format
        if fmt == "national" and e164.startswith("+260"):
            num = "0" + e164[4:]
        elif fmt == "international":
            num = "00" + e164[1:]
        elif fmt == "e164_no_plus":
            num = e164[1:]
        else:
            num = e164
        return f"PJSIP/{num}@{self.s.asterisk_trunk_endpoint}"

    async def originate(self, req: OriginateRequest) -> None:
        if not self.ami.connected:
            await self.report_outcome(req.call_id, "failed", f"AMI not connected: {self.ami.last_error}")
            return
        if not await capacity().try_acquire():
            await self.report_outcome(req.call_id, "failed", "capacity: max concurrent calls reached")
            return
        u = str(uuid.uuid4())
        self.pending[u] = PendingCall(u, "outbound", req.caller_id, req.to_number, "", call_id=req.call_id, capacity_reserved=True)
        self.by_call_id[req.call_id] = u
        try:
            resp = await self.ami.action(
                "Originate", action_id=f"orig-{req.call_id}", Channel=self.dial_string(req.to_number),
                Context=self.s.asterisk_outbound_context, Exten="s", Priority="1",
                CallerID=req.caller_id or "BongoHive", Timeout=str(req.timeout_s * 1000), Async="true",
                Variable=[f"VA_UUID={u}", f"VA_CALL_ID={req.call_id}"],
            )
            if resp.get("Response") != "Success":
                raise RuntimeError(resp.get("Message", "originate refused"))
            from ..services.calls import update_call

            await update_call(req.call_id, status="ringing", provider_call_id=u)
        except Exception as e:
            self.pending.pop(u, None)
            await capacity().release()
            await self.report_outcome(req.call_id, "failed", f"originate error: {e}")

    async def _on_ami_event(self, msg: dict) -> None:
        if msg.get("Event") != "OriginateResponse":
            return
        aid = msg.get("ActionID", "")
        if not aid.startswith("orig-"):
            return
        call_id = aid[5:]
        if msg.get("Response") == "Success":
            return  # answered; AudioSocket takes over
        outcome = _ORIGINATE_REASONS.get(msg.get("Reason", "0"), "failed")
        u = self.by_call_id.pop(call_id, None)
        p = self.pending.pop(u, None) if u else None
        if p and p.capacity_reserved:
            await capacity().release()
        await self.report_outcome(call_id, outcome, f"AMI reason {msg.get('Reason')}")

    async def redirect_to_human(self, channel: str, target: str) -> bool:
        if not channel or not self.ami.connected:
            return False
        try:
            if target.startswith("+"):
                target = self.dial_string(target)
            await self.ami.action("Setvar", Channel=channel, Variable="VA_TRANSFER_TARGET", Value=target)
            resp = await self.ami.action("Redirect", Channel=channel, Context=self.s.asterisk_transfer_context,
                                         Exten="s", Priority="1")
            return resp.get("Response") == "Success"
        except Exception:
            log.exception("Transfer failed")
            return False
