"""Streaming chat client for Ollama (native API) and any OpenAI-compatible API
(llama.cpp server, vLLM, Groq, OpenRouter, OpenAI, ...), with a fallback provider."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class Provider:
    kind: str  # ollama | openai
    base_url: str
    model: str
    api_key: str = ""

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.model}"


class LLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.providers = [Provider(s.llm_provider, s.llm_base_url.rstrip("/"), s.llm_model, s.llm_api_key.get_secret_value())]
        if s.llm_fallback_provider and s.llm_fallback_model:
            self.providers.append(Provider(
                s.llm_fallback_provider, s.llm_fallback_base_url.rstrip("/"), s.llm_fallback_model,
                s.llm_fallback_api_key.get_secret_value(),
            ))
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(s.llm_timeout_s, connect=5.0))
        self.last_error: str | None = None

    async def close(self) -> None:
        await self._http.aclose()

    # -------------------------------------------------------------- public API
    async def stream(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 200,
        json_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Tries each provider in turn if one fails before
        producing any output."""
        errors = []
        for prov in self.providers:
            produced = False
            try:
                gen = self._stream_one(prov, messages, temperature, max_tokens, json_mode)
                first = await asyncio.wait_for(anext(gen), timeout=self.settings.llm_first_token_timeout_s)
                produced = True
                yield first
                async for tok in gen:
                    yield tok
                self.last_error = None
                return
            except StopAsyncIteration:
                return
            except (asyncio.TimeoutError, httpx.HTTPError, LLMError, json.JSONDecodeError) as e:
                msg = f"{prov.label}: {type(e).__name__}: {e}"
                errors.append(msg)
                self.last_error = msg
                log.warning("LLM provider failed", extra={"ctx": {"provider": prov.label, "error": str(e) or type(e).__name__}})
                if produced:  # can't transparently switch mid-answer
                    raise LLMError(msg) from e
        raise LLMError("; ".join(errors) or "no LLM provider configured")

    async def complete(self, messages: list[dict], temperature: float = 0.2, max_tokens: int = 400, json_mode: bool = False) -> str:
        parts = [t async for t in self.stream(messages, temperature, max_tokens, json_mode)]
        return "".join(parts)

    async def health(self) -> dict:
        p = self.providers[0]
        t = time.perf_counter()
        try:
            if p.kind == "ollama":
                r = await self._http.get(f"{p.base_url}/api/tags", timeout=3)
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
                ok = any(n == p.model or n.split(":")[0] == p.model for n in names)
                return {"ok": ok, "provider": p.label, "latency_ms": round((time.perf_counter() - t) * 1000),
                        "detail": "model available" if ok else f"model '{p.model}' not pulled (ollama pull {p.model})"}
            r = await self._http.get(f"{p.base_url}/models", headers=self._headers(p), timeout=5)
            return {"ok": r.status_code < 400, "provider": p.label, "latency_ms": round((time.perf_counter() - t) * 1000),
                    "detail": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "provider": p.label, "detail": f"{type(e).__name__}: {e}"}

    async def warmup(self) -> None:
        p = self.providers[0]
        if p.kind == "ollama":
            try:
                await self._http.post(f"{p.base_url}/api/generate", json={"model": p.model, "prompt": "", "keep_alive": "30m"}, timeout=120)
            except Exception as e:
                log.warning("LLM warmup failed", extra={"ctx": {"error": str(e)}})

    # -------------------------------------------------------------- providers
    @staticmethod
    def _headers(p: Provider) -> dict:
        return {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}

    async def _stream_one(self, p: Provider, messages, temperature, max_tokens, json_mode) -> AsyncIterator[str]:
        if p.kind == "ollama":
            body = {
                "model": p.model, "messages": messages, "stream": True, "keep_alive": "30m",
                "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": self.settings.llm_num_ctx},
            }
            if json_mode:
                body["format"] = "json"
            async with self._http.stream("POST", f"{p.base_url}/api/chat", json=body) as r:
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {(await r.aread())[:300]!r}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise LLMError(data["error"])
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if data.get("done"):
                        return
        else:
            body = {"model": p.model, "messages": messages, "stream": True, "temperature": temperature, "max_tokens": max_tokens}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            async with self._http.stream("POST", f"{p.base_url}/chat/completions", json=body, headers=self._headers(p)) as r:
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {(await r.aread())[:300]!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    data = json.loads(payload)
                    choices = data.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if delta:
                            yield delta


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def set_llm(client: LLMClient | None) -> None:
    """Used by tests to inject a fake client."""
    global _client
    _client = client
