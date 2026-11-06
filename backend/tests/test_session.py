"""CallSession behaviour (turn-taking, guardrails, failures) with fake STT/TTS
and a fake transport, so these run fast on any machine. Real speech audio from
sample_data/audio is used for the VAD / barge-in tests."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.services.settings import AgentSettings
from app.voice import pipeline
from app.voice.pipeline import CallMeta, CallSession, Transport
from app.voice.stt import Transcript
from app.voice.tts import SynthResult

AUDIO = Path(__file__).resolve().parents[2] / "sample_data" / "audio"


class FakeTTS:
    def __init__(self, seconds_per_char=0.01):
        self.spc = seconds_per_char

    async def synth(self, text, engine, voice, speed=1.0, lang="en-us"):
        n = int(24000 * max(0.2, len(text) * self.spc))
        t = np.arange(n) / 24000
        return SynthResult((0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 1.0)


class FakeSTT:
    def __init__(self):
        self.next_text = "hello"
        self.calls = 0

    async def transcribe(self, audio, language="en", prompt=None):
        self.calls += 1
        return Transcript(self.next_text, "en", -0.2, 0.01, len(audio) / 16000, 5.0)


class FakeTransport(Transport):
    channel = "test"
    in_rate = 16000
    out_rate = 16000
    playback_lead_s = 0.05
    supports_transfer = True

    def __init__(self):
        self.events: list[dict] = []
        self.audio_bytes = 0
        self.cleared = 0
        self.hung_up = None
        self.transferred_to = None

    async def send_audio(self, pcm16):
        self.audio_bytes += len(pcm16)

    async def clear_audio(self):
        self.cleared += 1

    async def send_event(self, event):
        self.events.append(event)

    async def hangup(self, reason):
        self.hung_up = reason

    async def transfer(self, target):
        self.transferred_to = target
        return True

    def texts(self, kind):
        return [e.get("text") for e in self.events if e["type"] == kind]


@pytest.fixture
def fakes(monkeypatch, fake_llm, client):
    stt, tts = FakeSTT(), FakeTTS()
    monkeypatch.setattr(pipeline, "get_stt", lambda: stt)
    monkeypatch.setattr(pipeline, "get_tts", lambda: tts)
    fake_llm.replies = {}
    fake_llm.fail = False
    return stt, tts


def make_session(transport, **settings_over):
    s = AgentSettings()
    s.turn_taking.silence_reprompt_s = 3
    for k, v in settings_over.items():
        setattr(s, k, v)
    return CallSession(transport, CallMeta(channel="test", from_number="+260971234567", is_test=True), s)


async def wait_for(cond, timeout=10):
    t = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t < timeout:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def run(client, coro):
    return client.portal.call(lambda: coro)


def test_greeting_includes_ai_disclosure(client, fakes):
    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        assert await wait_for(lambda: s.state == "listening")
        s.request_end()
        await task
        return t
    t = run(client, scenario())
    greeting = " ".join(t.texts("assistant"))
    assert "AI assistant" in greeting
    assert t.audio_bytes > 0
    assert t.hung_up


def test_disclosure_given_when_caller_speaks_before_greeting_plays(client, fakes, fake_llm, monkeypatch):
    """Slow TTS (loaded CPU): the caller talks before the greeting has played, which cancels
    it. The agent must still say it is an AI before answering, and only once."""
    _, tts = fakes
    fast_synth = tts.synth

    async def slow_greeting(text, *a, **k):
        if "thank you for calling" in text:
            await asyncio.sleep(30)
        return await fast_synth(text, *a, **k)

    monkeypatch.setattr(tts, "synth", slow_greeting)
    fake_llm.replies = {"located": "We are at ZEP-RE Business Park on Alick Nkhata Road."}

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.3)  # greeting still being synthesised
        s.submit_text("Where are you located?")
        await wait_for(lambda: any("ZEP-RE" in (x or "") for x in t.texts("assistant")))
        await wait_for(lambda: s.state == "listening")
        s.submit_text("And are you open on Saturdays?")
        await wait_for(lambda: len(t.texts("assistant")) >= 4)
        await wait_for(lambda: s.state == "listening")
        s.request_end()
        await task
        return t
    said = run(client, scenario()).texts("assistant")
    first_reply = said.index(next(x for x in said if "ZEP-RE" in x))
    disclosures = [x for x in said if "an AI assistant" in x]
    assert disclosures and said.index(disclosures[0]) < first_reply, said
    assert len(disclosures) == 1, said


def test_text_turn_uses_knowledge_and_llm(client, fakes, fake_llm):
    fake_llm.replies = {"located": "We are at ZEP-RE Business Park on Alick Nkhata Road."}

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("Where are you located?")
        await wait_for(lambda: any("ZEP-RE" in (x or "") for x in t.texts("assistant")))
        await wait_for(lambda: s.state == "listening")
        s.request_end()
        await task
        return t, next(c for c in fake_llm.calls if c[-1]["content"] == "Where are you located?")
    t, msgs = run(client, scenario())
    assert any(e["type"] == "sources" for e in t.events)
    assert "ZEP-RE Business Park" in msgs[0]["content"]  # retrieved knowledge in system prompt
    assert any(e["type"] == "metrics" for e in t.events)


def test_control_tag_end_call(client, fakes, fake_llm):
    fake_llm.replies = {"that's everything": "Thank you for calling, goodbye! [END_CALL]"}

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("ok that's everything")
        return t, await asyncio.wait_for(task, 15)
    t, outcome = run(client, scenario())
    assert outcome == "completed"
    assert not any("[END_CALL]" in (x or "") for x in t.texts("assistant"))


def test_opt_out_is_deterministic(client, fakes):
    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("Please stop calling me")
        return await asyncio.wait_for(task, 15)
    assert run(client, scenario()) == "opted_out"
    contacts = client.get("/api/contacts?q=971234567").json()
    assert contacts and contacts[0]["consent_status"] == "revoked"


def test_human_request_transfers_when_possible(client, fakes, monkeypatch):
    from app.agent import guardrails
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "human_transfer_target", "PJSIP/reception")
    monkeypatch.setattr(guardrails, "within_hours", lambda h, now=None: True)

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("Can I speak to a human please")
        return t, await asyncio.wait_for(task, 15)
    t, outcome = run(client, scenario())
    assert outcome == "transferred"
    assert t.transferred_to == "PJSIP/reception"
    assert t.hung_up is None  # transferred calls are not hung up


def test_human_request_without_transfer_target_offers_callback(client, fakes, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "human_transfer_target", "")

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("I want to talk to someone real person")
        await wait_for(lambda: any("call you back" in (x or "") for x in t.texts("assistant")))
        s.request_end()
        return await task
    assert run(client, scenario()) == "escalated"


def test_llm_outage_falls_back_gracefully(client, fakes, fake_llm):
    fake_llm.fail = True

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("What programmes do you run?")
        await wait_for(lambda: any("technical problem" in (x or "") for x in t.texts("assistant")))
        s.request_end()
        await task
        return s
    s = run(client, scenario())
    fake_llm.fail = False
    assert s.escalated


def test_silence_reprompt_then_hangup(client, fakes):
    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        s.tt.silence_reprompt_s = 3
        s.tt.max_reprompts = 1
        task = asyncio.create_task(s.run())
        return t, await asyncio.wait_for(task, 30)
    t, outcome = run(client, scenario())
    assert outcome == "abandoned"
    assert any("still there" in (x or "") for x in t.texts("assistant"))


def _clip(name):
    audio, sr = sf.read(AUDIO / f"{name}.wav", dtype="int16")
    assert sr == 16000
    return audio


@pytest.mark.skipif(not (AUDIO / "q_location.wav").exists(), reason="run scripts/make_test_audio.py first")
def test_vad_endpointing_triggers_stt_on_real_speech(client, fakes):
    stt, _ = fakes
    stt.next_text = "Where are you located?"

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        pcm = _clip("q_location").tobytes() + np.zeros(16000, dtype=np.int16).tobytes()
        for i in range(0, len(pcm), 1024):
            s.feed_audio(pcm[i:i + 1024])
            await asyncio.sleep(0.005)
        await wait_for(lambda: stt.calls > 0)
        await wait_for(lambda: s.state == "listening", 15)
        s.request_end()
        await task
        return t
    t = run(client, scenario())
    assert stt.calls == 1
    assert {"type": "vad", "speaking": True} in t.events and {"type": "vad", "speaking": False} in t.events


@pytest.mark.skipif(not (AUDIO / "q_human.wav").exists(), reason="run scripts/make_test_audio.py first")
def test_barge_in_interrupts_agent(client, fakes, fake_llm):
    stt, tts = fakes
    tts.spc = 0.08  # long agent speech
    fake_llm.default = "Let me tell you all about our many programmes, " * 4
    stt.next_text = "Sorry, actually what is the address?"

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        s.submit_text("Tell me about programmes")
        await wait_for(lambda: s.state == "speaking")
        await asyncio.sleep(0.5)
        pcm = _clip("q_human").tobytes() + np.zeros(16000, dtype=np.int16).tobytes()
        for i in range(0, len(pcm), 1024):
            s.feed_audio(pcm[i:i + 1024])
            await asyncio.sleep(0.03)
        await wait_for(lambda: any(e["type"] == "interrupted" for e in t.events))
        await wait_for(lambda: stt.calls > 0, 10)
        s.request_end()
        await task
        return t, s
    fake_default = fake_llm.default
    try:
        t, s = run(client, scenario())
    finally:
        fake_llm.default = fake_default
    assert any(e["type"] == "interrupted" for e in t.events)
    assert t.cleared >= 1
    assert any(x.get("interrupted") for x in s.transcript if x["role"] == "assistant")


@pytest.mark.skipif(not (AUDIO / "q_location.wav").exists(), reason="run scripts/make_test_audio.py first")
def test_endpointing_when_far_end_stops_sending_frames(client, fakes):
    """Trunks with silence suppression send no frames while the caller is quiet."""
    stt, _ = fakes

    async def scenario():
        t = FakeTransport()
        s = make_session(t)
        task = asyncio.create_task(s.run())
        await wait_for(lambda: s.state == "listening")
        pcm = _clip("q_location").tobytes()  # speech only, no trailing silence frames
        for i in range(0, len(pcm), 1024):
            s.feed_audio(pcm[i:i + 1024])
            await asyncio.sleep(0.005)
        ok = await wait_for(lambda: stt.calls > 0, 5)
        s.request_end()
        await task
        return ok
    assert run(client, scenario())
