"""Real-time conversation engine shared by every channel (browser, simulator,
Asterisk). A Transport moves PCM audio in and out; CallSession does turn-taking
(VAD + endpointing + barge-in), STT, grounded LLM replies, TTS and call control.

    caller audio -> resample 16k -> Silero VAD -> utterance -> Whisper
       -> guardrails -> retrieval -> LLM (streamed) -> sentence chunker
       -> Kokoro TTS -> paced 20 ms frames -> caller
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..agent import guardrails
from ..agent.conversation import CallContext, Conversation
from ..config import get_settings
from ..db import utcnow
from ..llm.client import LLMError
from ..services import calls as call_store
from ..services.events import bus, live
from ..services.settings import AgentSettings
from .audio import StreamResampler, float_to_pcm16, pcm16_to_float, resample
from .recorder import Recorder
from .stt import get_stt
from .tts import OUTPUT_RATE, SentenceChunker, get_tts
from .vad import CHUNK, RATE, StreamingVAD

log = logging.getLogger(__name__)
_AI_WORD = re.compile(r"\b(ai|a\.i\.)(?=\W|$)", re.I)

FRAME_MS = 20
CHUNK_MS = CHUNK * 1000 / RATE  # 32 ms
_BACKGROUND: set[asyncio.Task] = set()  # post-call jobs outliving their session


class Transport(ABC):
    """What a channel must provide to host a CallSession."""

    channel = "generic"
    in_rate = 16000  # sample rate of PCM16 we receive
    out_rate = 24000  # sample rate of PCM16 we send
    playback_lead_s = 0.12  # how far ahead of real time we may push audio
    supports_transfer = False

    @abstractmethod
    async def send_audio(self, pcm16: bytes) -> None: ...

    async def clear_audio(self) -> None:  # drop anything buffered on the far side
        pass

    async def send_event(self, event: dict) -> None:
        pass

    async def hangup(self, reason: str) -> None:
        pass

    async def transfer(self, target: str) -> bool:
        return False


@dataclass
class CallMeta:
    direction: str = "inbound"
    channel: str = "browser"
    from_number: str = ""
    to_number: str = ""
    contact_id: str | None = None
    contact_name: str = ""
    campaign_id: str | None = None
    campaign_name: str = ""
    campaign_script: str = ""
    opening_line: str = ""
    voicemail_action: str = "hangup"
    voicemail_message: str = ""
    provider_call_id: str | None = None
    call_id: str | None = None  # existing Call row (outbound) or None to create one
    is_test: bool = False


@dataclass
class _Utterance:
    audio: list[np.ndarray] = field(default_factory=list)
    speech_chunks: int = 0
    silence_chunks: int = 0
    started: float = 0.0

    @property
    def duration_s(self) -> float:
        return len(self.audio) * CHUNK / RATE


@dataclass
class _PlayItem:
    audio: np.ndarray  # at transport out_rate
    text: str
    turn_id: int


class CallSession:
    def __init__(self, transport: Transport, meta: CallMeta, settings: AgentSettings) -> None:
        self.t = transport
        self.meta = meta
        self.s = settings
        self.tt = settings.turn_taking
        self.conv = Conversation(settings, CallContext(
            direction=meta.direction, caller_number=meta.from_number if meta.direction == "inbound" else meta.to_number,
            contact_name=meta.contact_name, campaign_name=meta.campaign_name, campaign_script=meta.campaign_script,
            channel=meta.channel,
        ))
        self.call_id: str = meta.call_id or ""
        self.stt = get_stt()
        self.tts = get_tts()
        self.vad = StreamingVAD()
        self.in_resampler = StreamResampler(transport.in_rate, RATE)
        self.recorder = Recorder(transport.in_rate, transport.out_rate) if settings.recording_enabled and not meta.is_test else None

        self.state = "starting"  # listening | thinking | speaking
        self._in_q: asyncio.Queue[bytes | str | None] = asyncio.Queue(maxsize=500)
        self._play_q: asyncio.Queue[_PlayItem] = asyncio.Queue()
        self._preroll: deque[np.ndarray] = deque(maxlen=10)
        self._utt: _Utterance | None = None
        self._speech_run = 0
        self._response_task: asyncio.Task | None = None
        self._player_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._db_tasks: set[asyncio.Task] = set()
        self._playing = False
        self._now_playing = ""  # sentence currently being played (for barge-in bookkeeping)
        self._turn_id = 0
        self._spoken: dict[int, list[str]] = {}
        self._pending_user_text = ""
        self._audio_sent_for_turn = False
        self._last_activity = time.monotonic()
        self._last_audio_at = time.monotonic()
        self._reprompts = 0
        self._started = time.monotonic()
        self._ended = asyncio.Event()
        self.outcome = ""
        self.end_reason = ""
        self.escalated = False
        self.transferred = False
        self._end_after_playback: str | None = None
        self.metrics: dict[str, list[float]] = {"stt_ms": [], "llm_first_token_ms": [], "first_audio_ms": [], "tts_ms": [], "turn_ms": []}
        self.transcript: list[dict] = []
        self._first_utterance_seen = False
        self._greeted = False
        self._greeting_turn = -1  # turn id of the greeting, to check the AI disclosure was actually heard

    # ================================================================ public API
    def feed_audio(self, pcm16: bytes) -> None:
        """Called by the transport for every inbound audio frame (any size)."""
        if self.recorder:
            self.recorder.add_inbound(pcm16)
        try:
            self._in_q.put_nowait(pcm16)
        except asyncio.QueueFull:  # we are falling behind; drop oldest
            try:
                self._in_q.get_nowait()
                self._in_q.put_nowait(pcm16)
            except asyncio.QueueEmpty:
                pass

    def submit_text(self, text: str) -> None:
        """Typed input (test console). Bypasses STT."""
        self._in_q.put_nowait("TEXT:" + text)

    def request_end(self, outcome: str = "completed", reason: str = "remote_hangup") -> None:
        if not self._ended.is_set():
            if outcome != "completed":  # a plain hangup must not mask e.g. an escalation
                self.outcome = self.outcome or outcome
            self.end_reason = self.end_reason or reason
            self._ended.set()
            self._in_q.put_nowait(None)

    async def run(self) -> str:
        """Drive the call until it ends. Returns the outcome."""
        await self._start()
        self._player_task = asyncio.create_task(self._player())
        try:
            if self.meta.direction == "inbound":
                self._response_task = asyncio.create_task(self._greet_then_listen())
            else:
                self._spawn(self._outbound_greet_timer())
            while not self._ended.is_set():
                try:
                    item = await asyncio.wait_for(self._in_q.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    item = b""
                if item is None:
                    break
                if isinstance(item, str) and item.startswith("TEXT:"):
                    self._last_activity = time.monotonic()
                    await self._on_user_text(item[5:], stt_ms=0.0)
                elif item:
                    await self._process_audio(item)
                await self._check_audio_gap()
                await self._check_timers()
        except Exception as e:
            log.exception("Call session crashed", extra={"ctx": {"call_id": self.call_id}})
            self.outcome = self.outcome or "failed"
            self.end_reason = self.end_reason or f"error: {e}"
        finally:
            await self._finish()
        return self.outcome

    # ================================================================ lifecycle
    async def _start(self) -> None:
        now = utcnow()
        if self.call_id:
            await call_store.update_call(self.call_id, status="in_progress", answered_at=now, started_at=now)
        else:
            call = await call_store.create_call(
                direction=self.meta.direction, channel=self.meta.channel, from_number=self.meta.from_number,
                to_number=self.meta.to_number, contact_id=self.meta.contact_id, campaign_id=self.meta.campaign_id,
                provider_call_id=self.meta.provider_call_id, status="in_progress", started_at=now, answered_at=now,
            )
            self.call_id = call.id
        live.update(self.call_id, direction=self.meta.direction, channel=self.meta.channel,
                    from_number=self.meta.from_number, to_number=self.meta.to_number, state="starting",
                    started_at=now.isoformat(), campaign=self.meta.campaign_name)
        await self.t.send_event({"type": "call_started", "call_id": self.call_id})
        await bus.publish("call.started", {"call_id": self.call_id, "direction": self.meta.direction,
                                           "from": self.meta.from_number, "to": self.meta.to_number})
        log.info("Call started", extra={"ctx": {"call_id": self.call_id, "dir": self.meta.direction, "channel": self.meta.channel}})

    async def _finish(self) -> None:
        self._ended.set()
        for task in [self._response_task, self._player_task, *self._tasks]:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in [self._response_task, self._player_task, *self._tasks] if t], return_exceptions=True)
        if self._db_tasks:
            await asyncio.gather(*list(self._db_tasks), return_exceptions=True)
        if not self.transferred:
            try:
                await self.t.hangup(self.end_reason or "completed")
            except Exception:
                log.warning("Transport hangup failed", exc_info=True)
        outcome = self.outcome or "completed"
        if self.escalated and outcome == "completed":
            outcome = "escalated"
        self.outcome = outcome
        duration = time.monotonic() - self._started
        rec_path = None
        if self.recorder:
            try:
                rec_path = await asyncio.to_thread(self.recorder.save, self.call_id)
            except Exception:
                log.exception("Saving recording failed")
        summary_metrics = {k: {"avg": round(float(np.mean(v))), "p95": round(float(np.percentile(v, 95))), "n": len(v)}
                           for k, v in self.metrics.items() if v}
        await call_store.update_call(
            self.call_id, status="completed" if outcome != "failed" else "failed", outcome=outcome,
            hangup_cause=self.end_reason[:100], ended_at=utcnow(), duration_s=round(duration, 1),
            turn_count=sum(1 for t in self.transcript if t["role"] == "user"), metrics=summary_metrics,
            recording_path=rec_path,
        )
        live.remove(self.call_id)
        await self.t.send_event({"type": "call_ended", "call_id": self.call_id, "outcome": outcome, "metrics": summary_metrics})
        log.info("Call ended", extra={"ctx": {"call_id": self.call_id, "outcome": outcome, "duration_s": round(duration, 1)}})
        from ..services.postcall import process_completed_call

        task = asyncio.create_task(process_completed_call(self.call_id, list(self.transcript), self.meta, outcome, self.escalated))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)

    def _end(self, outcome: str, reason: str) -> None:
        self.outcome = self.outcome or outcome
        self.end_reason = self.end_reason or reason
        self._ended.set()
        try:
            self._in_q.put_nowait(None)
        except asyncio.QueueFull:
            pass

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            live.update(self.call_id, state=state)
            await self.t.send_event({"type": "state", "state": state})

    # ================================================================ greeting
    async def _greet(self) -> None:
        if self._greeted:
            return
        self._greeted = True
        if self.meta.direction == "outbound":
            text = self.conv.fmt(self.meta.opening_line or self.s.greeting_outbound)
        else:
            text = self.conv.fmt(self.s.greeting_inbound)
        if self.s.ai_disclosure_required and "ai" not in text.lower() and "a.i." not in text.lower():
            text = f"Hi, I'm {self.s.agent_name}, an AI assistant for BongoHive. " + text
        if self.s.recording_enabled:
            text += " " + self.s.recording_disclosure
        self._greeting_turn = self._turn_id + 1
        await self._say(text, record=True)

    def _disclosure_heard(self) -> bool:
        """True once a greeting sentence stating the agent is an AI has fully played.
        A greeting that is still being synthesised, or cut off by the caller, does not count."""
        return any(_AI_WORD.search(s) for s in self._spoken.get(self._greeting_turn, []))

    async def _ensure_disclosure(self, turn: int) -> None:
        """Speak a short AI disclosure at the start of reply `turn` if the caller never heard it."""
        if not self.s.ai_disclosure_required or self._disclosure_heard():
            return
        if self.meta.direction == "outbound":
            text = f"Sorry, I should say: this is {self.s.agent_name}, an AI assistant calling on behalf of BongoHive."
        else:
            text = f"Just so you know, I'm {self.s.agent_name}, an AI assistant for BongoHive."
        self._greeting_turn = turn
        await self._speak_sentence(text, turn)
        self.conv.add_assistant(text)
        self._record_turn("assistant", text)

    async def _greet_then_listen(self) -> None:
        await self._greet()
        await self._wait_playback()
        await self._set_state("listening")
        self._last_activity = time.monotonic()

    async def _outbound_greet_timer(self) -> None:
        # Let the callee say "Hello?" first; greet after it or after a short pause.
        await asyncio.sleep(1.5)
        if not self._greeted and not self._utt and not (self._response_task and not self._response_task.done()):
            self._response_task = asyncio.create_task(self._greet_then_listen())

    # ================================================================ audio input
    async def _process_audio(self, pcm16: bytes) -> None:
        audio = self.in_resampler(pcm16_to_float(pcm16))
        thr = self.tt.vad_threshold
        agent_busy = self.state in ("speaking", "thinking") or self._playing
        start_thr = min(thr + 0.15, 0.9) if agent_busy else thr  # resist echo / backchannels
        self._last_audio_at = time.monotonic()
        for chunk, prob in self.vad.process(audio):
            if self._utt is None:
                self._preroll.append(chunk)
                if prob >= start_thr:
                    self._speech_run += 1
                else:
                    self._speech_run = 0
                if self._speech_run * CHUNK_MS >= self.tt.min_speech_ms:
                    self._utt = _Utterance(audio=list(self._preroll), speech_chunks=self._speech_run, started=time.monotonic())
                    self._preroll.clear()
                    self._speech_run = 0
                    await self.t.send_event({"type": "vad", "speaking": True})
                continue

            u = self._utt
            u.audio.append(chunk)
            if prob >= thr - 0.15:
                u.speech_chunks += 1
                u.silence_chunks = 0
            else:
                u.silence_chunks += 1
            self._last_activity = time.monotonic()

            # barge-in: caller keeps talking over the agent
            if agent_busy and self.tt.barge_in_enabled and u.speech_chunks * CHUNK_MS >= self.tt.barge_in_min_ms:
                await self._interrupt()
                agent_busy = False

            if u.silence_chunks * CHUNK_MS >= self.tt.endpoint_silence_ms or u.duration_s >= self.tt.max_utterance_s:
                await self._finish_utterance()

    async def _finish_utterance(self) -> None:
        u, self._utt = self._utt, None
        if u is None:
            return
        await self.t.send_event({"type": "vad", "speaking": False})
        speech_ms = u.speech_chunks * CHUNK_MS
        if (self.state == "speaking" or self._playing) and speech_ms < self.tt.barge_in_min_ms:
            return  # short backchannel ("mm-hmm") while agent talks: ignore
        if self._response_task and not self._response_task.done():
            if not self.tt.barge_in_enabled:
                return
            await self._interrupt()
        self._response_task = asyncio.create_task(self._handle_utterance(np.concatenate(u.audio)))

    async def _check_audio_gap(self) -> None:
        """Endpoint on wall-clock time when the far end stops sending frames
        mid-utterance (trunks with silence suppression / DTX, Local channels)."""
        if self._utt is not None and time.monotonic() - self._last_audio_at > self.tt.endpoint_silence_ms / 1000:
            await self._finish_utterance()

    # ================================================================ turns
    async def _handle_utterance(self, audio: np.ndarray) -> None:
        t_end = time.perf_counter()
        await self._set_state("thinking")
        try:
            tr = await self.stt.transcribe(audio)
        except Exception as e:
            log.exception("STT failed", extra={"ctx": {"call_id": self.call_id}})
            await self._say(self.s.fallback_error)
            await self._set_state("listening")
            return
        self.metrics["stt_ms"].append(tr.elapsed_ms)
        if not tr.text or len(tr.text) < 2 or tr.no_speech_prob > 0.85:
            log.debug("Discarded empty utterance", extra={"ctx": {"nsp": tr.no_speech_prob}})
            await self._set_state("listening")
            return
        await self._on_user_text(tr.text, stt_ms=tr.elapsed_ms, t_end=t_end)

    async def _on_user_text(self, text: str, stt_ms: float, t_end: float | None = None) -> None:
        t_end = t_end or time.perf_counter()
        text = text.strip()
        if self._pending_user_text:  # caller resumed after cutting the agent off before it spoke
            text = f"{self._pending_user_text} {text}"
            self._pending_user_text = ""
        self._reprompts = 0
        self._record_turn("user", text)
        await self.t.send_event({"type": "transcript", "role": "user", "text": text})
        if self._response_task and self._response_task is not asyncio.current_task() and not self._response_task.done():
            await self._interrupt()
        if asyncio.current_task() is self._response_task:
            await self._respond(text, stt_ms, t_end)
        else:
            self._response_task = asyncio.create_task(self._respond(text, stt_ms, t_end))

    async def _respond(self, text: str, stt_ms: float, t_end: float) -> None:
        self._turn_id += 1
        turn = self._turn_id
        self._spoken[turn] = []
        self._audio_sent_for_turn = False
        await self._set_state("thinking")

        # ---- first utterance on an outbound call may be voicemail
        if self.meta.direction == "outbound" and not self._first_utterance_seen:
            self._first_utterance_seen = True
            if guardrails.looks_like_voicemail(text):
                await self._handle_voicemail()
                return
            if not self._greeted:
                await self._greet()
                await self._wait_playback()
                await self._set_state("listening")
                return
        self._first_utterance_seen = True
        await self._ensure_disclosure(turn)

        # ---- deterministic guardrails
        if guardrails.wants_opt_out(text):
            phone = self.meta.to_number if self.meta.direction == "outbound" else self.meta.from_number
            await call_store.opt_out(phone, source=f"call:{self.call_id}", call_id=self.call_id)
            await self._say("I understand, and I'm sorry for the disturbance. I've removed your number and you won't be "
                            "called again. Goodbye.", record=True)
            await self._wait_playback()
            self._end("opted_out", "caller_opted_out")
            return
        if guardrails.wants_human(text, self.s.escalation.keywords):
            self.conv.add_user(text)
            await self._escalate(reason=text)
            return

        # ---- grounded LLM reply
        self.conv.add_user(text)
        t_ret = time.perf_counter()
        hits = await self.conv.retrieve_async(text)
        retrieval_ms = (time.perf_counter() - t_ret) * 1000
        sources = [{"title": h.source_title, "heading": h.heading, "score": round(h.dense, 3), "sample": h.is_sample} for h in hits]
        if sources:
            await self.t.send_event({"type": "sources", "items": sources})

        sentences: list[str] = []
        first_audio_ms = None
        tts_ms = 0.0
        tags: set[str] = set()
        try:
            async for sentence in self.conv.reply_sentences(hits):
                sentences.append(sentence)
                tts_ms += await self._speak_sentence(sentence, turn)
                if first_audio_ms is None:
                    first_audio_ms = (time.perf_counter() - t_end) * 1000
            tags = self.conv.tags
        except asyncio.CancelledError:
            raise
        except LLMError:
            log.error("LLM unavailable during call", extra={"ctx": {"call_id": self.call_id}})
            sentences = [self.s.fallback_error]
            await self._speak_sentence(self.s.fallback_error, turn)
            self.escalated = True
        first_tok_ms = self.conv.first_token_ms

        reply = " ".join(sentences).strip()
        if not reply:
            reply = "Sorry, could you say that again?"
            await self._speak_sentence(reply, turn)
        self.conv.add_assistant(reply)
        if guardrails.admits_unknown(reply):
            self.conv.unanswered += 1
        latency = {"stt_ms": round(stt_ms), "retrieval_ms": round(retrieval_ms),
                   "llm_first_token_ms": round(first_tok_ms or 0), "first_audio_ms": round(first_audio_ms or 0),
                   "tts_ms": round(tts_ms)}
        if self.conv.blocked:
            latency["blocked_claim"] = self.conv.blocked["reason"]
        if first_tok_ms:
            self.metrics["llm_first_token_ms"].append(first_tok_ms)
        if first_audio_ms:
            self.metrics["first_audio_ms"].append(first_audio_ms)
            self.metrics["turn_ms"].append(first_audio_ms)
        self.metrics["tts_ms"].append(tts_ms)
        self._record_turn("assistant", reply, latency=latency, sources=sources)
        await self.t.send_event({"type": "metrics", "latency": latency})

        await self._wait_playback()
        if "[TRANSFER]" in tags:
            await self._escalate(reason="agent_initiated", announce=False)
            return
        if "[END_CALL]" in tags or guardrails.is_goodbye(text):
            self._end("completed", "conversation_finished")
            return
        await self._set_state("listening")
        self._last_activity = time.monotonic()

    # ================================================================ speech output
    async def _speak_sentence(self, sentence: str, turn: int) -> float:
        v = self.s.voice
        try:
            res = await self.tts.synth(sentence, v.engine, v.voice, v.speed, v.lang)
        except Exception:
            log.exception("TTS failed", extra={"ctx": {"engine": v.engine}})
            if v.engine != "piper":
                try:
                    res = await self.tts.synth(sentence, "piper", "", v.speed)
                except Exception:
                    return 0.0
            else:
                return 0.0
        audio = resample(res.audio, OUTPUT_RATE, self.t.out_rate)
        await self._play_q.put(_PlayItem(audio, sentence, turn))
        if self.state != "speaking":
            await self._set_state("speaking")
        await self.t.send_event({"type": "assistant", "text": sentence, "turn": turn})
        return res.elapsed_ms

    async def _say(self, text: str, record: bool = True) -> None:
        self._turn_id += 1
        turn = self._turn_id
        self._spoken[turn] = []
        chunker = SentenceChunker(first_min_chars=10_000)
        for sentence in chunker.feed(text) + chunker.flush():
            await self._speak_sentence(sentence, turn)
        if record:
            self.conv.add_assistant(text)
            self._record_turn("assistant", text)

    async def _player(self) -> None:
        """Send queued audio as 20 ms frames paced to real time (+ small lead)."""
        rate = self.t.out_rate
        frame = rate * FRAME_MS // 1000
        clock = time.monotonic()
        while True:
            item = await self._play_q.get()
            self._playing = True
            self._now_playing = item.text
            pcm = float_to_pcm16(item.audio)
            step = frame * 2
            now = time.monotonic()
            if clock < now:
                clock = now
            for i in range(0, len(pcm), step):
                chunk = pcm[i:i + step]
                if len(chunk) < step:
                    chunk += b"\x00" * (step - len(chunk))
                await self.t.send_audio(chunk)
                if self.recorder:
                    self.recorder.add_outbound(chunk)
                self._audio_sent_for_turn = True
                clock += FRAME_MS / 1000
                delay = clock - time.monotonic() - self.t.playback_lead_s
                if delay > 0:
                    await asyncio.sleep(delay)
            self._spoken.setdefault(item.turn_id, []).append(item.text)
            self._now_playing = ""
            if self._play_q.empty():
                # wait for the far end to actually finish the buffered lead
                remaining = clock - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                if self._play_q.empty():
                    self._playing = False
                    self._last_activity = time.monotonic()

    async def _wait_playback(self) -> None:
        while not self._play_q.empty() or self._playing:
            await asyncio.sleep(0.03)

    async def _interrupt(self) -> None:
        """Caller barged in: stop speaking and cancel the in-flight reply."""
        task = self._response_task
        was_speaking = self._playing or not self._play_q.empty()
        cut_off = self._now_playing if self._playing else ""
        while not self._play_q.empty():
            self._play_q.get_nowait()
        if self._player_task and self._playing:
            self._player_task.cancel()
            await asyncio.gather(self._player_task, return_exceptions=True)
            self._player_task = asyncio.create_task(self._player())
            self._playing = False
        await self.t.clear_audio()
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            spoken = " ".join(self._spoken.get(self._turn_id, []) + ([cut_off] if cut_off else []))
            if spoken:
                self.conv.add_assistant(spoken + " ...")
                self._record_turn("assistant", spoken, interrupted=True)
            elif not self._audio_sent_for_turn and self.conv.history and self.conv.history[-1]["role"] == "user":
                # nothing was said yet: merge the earlier text with what comes next
                self._pending_user_text = self.conv.history.pop()["content"]
                if self.transcript and self.transcript[-1]["role"] == "user":
                    self.transcript[-1]["merged"] = True
        if was_speaking:
            await self.t.send_event({"type": "interrupted"})
            log.debug("Barge-in", extra={"ctx": {"call_id": self.call_id}})
        await self._set_state("listening")

    # ================================================================ escalation etc.
    async def _escalate(self, reason: str, announce: bool = True) -> None:
        esc = self.s.escalation
        self.escalated = True
        await bus.publish("call.escalated", {"call_id": self.call_id, "reason": reason[:500],
                                             "phone": self.meta.from_number if self.meta.direction == "inbound" else self.meta.to_number})
        target = get_settings().human_transfer_target
        can_transfer = (esc.transfer_enabled and self.t.supports_transfer and bool(target)
                        and (not esc.transfer_only_in_hours or guardrails.within_hours(self.s.hours)))
        if can_transfer:
            if announce:
                await self._say(esc.transfer_message)
            await self._wait_playback()
            ok = await self.t.transfer(target)
            if ok:
                self.transferred = True
                await bus.publish("call.transferred", {"call_id": self.call_id, "target": target})
                self._end("transferred", "transferred_to_human")
                return
        await self._say(esc.unavailable_message + " Is there anything else I can help you with in the meantime?")
        await self._wait_playback()
        await self._set_state("listening")
        self._last_activity = time.monotonic()

    async def _handle_voicemail(self) -> None:
        log.info("Voicemail detected", extra={"ctx": {"call_id": self.call_id}})
        if self.meta.voicemail_action == "leave_message" and self.meta.voicemail_message:
            await asyncio.sleep(1.0)  # wait for the beep
            await self._say(self.conv.fmt(self.meta.voicemail_message))
            await self._wait_playback()
        self._end("voicemail", "voicemail_detected")

    async def _check_timers(self) -> None:
        now = time.monotonic()
        if now - self._started > self.tt.max_call_minutes * 60 and self.state == "listening":
            await self._say("I'm sorry, we've reached the maximum call length. A member of the team can follow up with you. Goodbye.")
            await self._wait_playback()
            self._end("completed", "max_duration")
            return
        busy = self._utt is not None or self._playing or self.state != "listening" or (
            self._response_task is not None and not self._response_task.done())
        if busy or not self._greeted:
            return
        if now - self._last_activity > self.tt.silence_reprompt_s:
            self._last_activity = now
            if self._reprompts < self.tt.max_reprompts:
                self._reprompts += 1
                self._response_task = asyncio.create_task(self._reprompt(self.s.reprompt))
            else:
                self._response_task = asyncio.create_task(self._silence_goodbye())

    async def _reprompt(self, text: str) -> None:
        await self._say(text, record=True)
        await self._wait_playback()
        await self._set_state("listening")
        self._last_activity = time.monotonic()

    async def _silence_goodbye(self) -> None:
        await self._say(self.s.no_response_goodbye)
        await self._wait_playback()
        self._end("abandoned", "caller_silent")

    def _record_turn(self, role: str, text: str, interrupted: bool = False, latency: dict | None = None,
                     sources: list | None = None) -> None:
        self.transcript.append({"role": role, "text": text, "interrupted": interrupted})
        live.transcript(self.call_id, role, text)
        task = asyncio.create_task(call_store.add_turn(self.call_id, role, text, interrupted, latency, sources))
        self._db_tasks.add(task)
        task.add_done_callback(self._db_tasks.discard)
