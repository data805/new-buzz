"""Test fixtures. Tests run against a throwaway SQLite database, a fake LLM and
(for most tests) without loading the heavy speech models."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="voiceagent-test-"))
os.environ.update({
    "ENVIRONMENT": "test",
    # set TEST_DATABASE_URL to run the suite against a (migrated, empty) PostgreSQL database
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmp / 'test.db'}"),
    "DATA_DIR": str(_tmp),
    "RECORDINGS_DIR": str(_tmp / "recordings"),
    "UPLOADS_DIR": str(_tmp / "uploads"),
    "PRELOAD_MODELS": "false",
    "TELEPHONY_PROVIDER": "simulator",
    "ADMIN_EMAIL": "admin@test.local",
    "ADMIN_PASSWORD": "AdminPass123",
    "OUTBOUND_ENABLED": "true",
    "OUTBOUND_MIN_HOURS_BETWEEN_CALLS": "24",
    "STT_MODEL": "base.en",
    "TTS_ENGINE": "kokoro",
    "TTS_KOKORO_MODEL": "kokoro/kokoro-v1.0.int8.onnx",
    "WEBHOOK_ALLOWED_HOSTS": "hooks.example.com",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.llm.client import set_llm  # noqa: E402


class FakeLLM:
    """Deterministic stand-in for the language model. `replies` maps a
    substring of the last user message to a reply; JSON mode returns `extraction`."""

    def __init__(self):
        self.replies: dict[str, str] = {}
        self.default = "I'm sorry, I don't have that information. Can I take your details for a callback?"
        self.extraction = '{"summary": "test call", "intent": "general_enquiry", "team": "general", "sentiment": "neutral", "resolved": true, "callback_requested": false, "needs_human": false, "action_items": []}'
        self.calls: list[list[dict]] = []
        self.fail = False
        self.last_error = None

    async def stream(self, messages, temperature=0.3, max_tokens=200, json_mode=False):
        from app.llm.client import LLMError

        self.calls.append(messages)
        if self.fail:
            raise LLMError("simulated outage")
        if json_mode:
            text = self.extraction
        else:
            last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            text = next((v for k, v in self.replies.items() if k.lower() in last.lower()), self.default)
        for i in range(0, len(text), 7):
            yield text[i:i + 7]
            await asyncio.sleep(0)

    async def complete(self, messages, temperature=0.2, max_tokens=400, json_mode=False):
        return "".join([t async for t in self.stream(messages, temperature, max_tokens, json_mode)])

    async def health(self):
        return {"ok": True, "provider": "fake"}

    async def warmup(self):
        pass

    async def close(self):
        pass


@pytest.fixture(scope="session")
def fake_llm():
    llm = FakeLLM()
    set_llm(llm)
    return llm


@pytest.fixture(scope="session")
def client(fake_llm):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"email": "admin@test.local", "password": "AdminPass123"})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.security import rate_limiter

    rate_limiter.reset()
    yield
