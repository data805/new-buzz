"""Prompt assembly and grounded response generation for one call."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ..knowledge.store import Hit, index
from ..llm.client import get_llm
from ..services.settings import AgentSettings
from .guardrails import within_hours

log = logging.getLogger(__name__)


@dataclass
class CallContext:
    direction: str = "inbound"
    caller_number: str = ""
    contact_name: str = ""
    campaign_name: str = ""
    campaign_script: str = ""
    channel: str = "browser"


@dataclass
class Conversation:
    settings: AgentSettings
    ctx: CallContext
    history: list[dict] = field(default_factory=list)
    unanswered: int = 0
    escalation_offered: bool = False

    def fmt(self, text: str) -> str:
        return text.replace("{agent_name}", self.settings.agent_name)

    def retrieve(self, query: str) -> list[Hit]:
        k, min_score = self.settings.knowledge_top_k, self.settings.knowledge_min_score
        hits = index.search(query, top_k=k, min_score=min_score)
        # short follow-ups ("how much is it?") also retrieve with the previous user turn as
        # context; the current question's own hits keep priority
        prev = next((m["content"] for m in reversed(self.history[:-1]) if m["role"] == "user"), "")
        if prev and len(query.split()) < 6:
            seen = {h.chunk_id for h in hits}
            hits += [h for h in index.search(f"{prev} {query}", top_k=k, min_score=min_score) if h.chunk_id not in seen]
        return hits[:k]

    def system_prompt(self, hits: list[Hit]) -> str:
        s = self.settings
        tz = ZoneInfo(s.hours.timezone)
        now = datetime.now(tz)
        open_now = within_hours(s.hours)
        days = ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in s.hours.days)
        parts = [self.fmt(s.persona).strip()]
        parts.append(
            f"\nCALL CONTEXT\n- Current time in Lusaka: {now:%A %d %B %Y, %H:%M}.\n"
            f"- Office hours: {days} {s.hours.open}-{s.hours.close}. The office is currently {'OPEN' if open_now else 'CLOSED'}."
            + ("" if open_now else f"\n- Out of hours guidance: {s.hours.after_hours_message}")
            + f"\n- Call direction: {self.ctx.direction}."
            + (f"\n- Caller's number: {self.ctx.caller_number}." if self.ctx.caller_number else "")
            + (f"\n- Caller's name on file: {self.ctx.contact_name}." if self.ctx.contact_name else "")
        )
        if self.ctx.direction == "outbound" and self.ctx.campaign_script:
            parts.append(
                f"\nOUTBOUND CALL PURPOSE ({self.ctx.campaign_name})\n{self.ctx.campaign_script.strip()}\n"
                "If the person says it is not a good time, offer to call back later and end politely. "
                "If they ask not to be called again, apologise, confirm they will not be called again, and end the call."
            )
        if hits:
            kb = "\n\n".join(
                f"[{i + 1}] {h.source_title}{' - ' + h.heading if h.heading else ''}\n{h.text}" for i, h in enumerate(hits)
            )
            parts.append(f"\nKNOWLEDGE (verified BongoHive information; the only source for company facts)\n{kb}")
        else:
            parts.append(
                "\nKNOWLEDGE\n(No relevant company information was found for this question. If the caller is asking "
                "about BongoHive specifics, say you don't have that information and offer a callback from the team.)"
            )
        if self.unanswered >= s.escalation.max_unanswered and not self.escalation_offered:
            parts.append("\nNOTE: You have been unable to answer several questions. Offer to connect the caller to a "
                         "staff member or take their details for a callback.")
            self.escalation_offered = True
        return "\n".join(parts)

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        if text.strip():
            self.history.append({"role": "assistant", "content": text.strip()})

    def messages(self, hits: list[Hit]) -> list[dict]:
        n = self.settings.history_turns * 2
        return [{"role": "system", "content": self.system_prompt(hits)}] + self.history[-n:]

    async def stream_reply(self, hits: list[Hit]):
        llm = get_llm()
        async for tok in llm.stream(self.messages(hits), temperature=self.settings.llm_temperature,
                                    max_tokens=self.settings.llm_max_tokens):
            yield tok

    async def reply_sentences(self, hits: list[Hit]):
        """Stream the reply as speakable sentences: control tags stripped, and any
        sentence with unsupported specifics (prices, numbers, contacts) replaced by
        an honest fallback, which also ends the reply. Sets `tags`, `blocked`
        and `first_token_ms` for the caller."""
        import time

        from ..voice.tts import SentenceChunker
        from .factcheck import check_sentence
        from .guardrails import TagFilter

        tag_filter, chunker = TagFilter(), SentenceChunker()
        self.tags, self.blocked, self.first_token_ms = tag_filter.tags, None, None
        context = "\n".join([self.system_prompt(hits)] + [m["content"] for m in self.history])
        t0 = time.perf_counter()

        def vet(sentence: str) -> str | None:
            fc = check_sentence(sentence, context)
            if fc.ok:
                return sentence
            self.blocked = {"sentence": sentence, "reason": fc.reason}
            log.warning("Blocked unsupported claim", extra={"ctx": {"reason": fc.reason, "sentence": sentence[:200]}})
            return fc.fallback

        async for tok in self.stream_reply(hits):
            if self.first_token_ms is None:
                self.first_token_ms = (time.perf_counter() - t0) * 1000
            for s in chunker.feed(tag_filter.feed(tok)):
                out = vet(s)
                yield out
                if self.blocked:
                    return
        for s in chunker.feed(tag_filter.flush()) + chunker.flush():
            out = vet(s)
            yield out
            if self.blocked:
                return

    async def retrieve_async(self, query: str) -> list[Hit]:
        return await asyncio.to_thread(self.retrieve, query)
