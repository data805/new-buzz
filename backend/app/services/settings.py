"""Runtime agent configuration, editable from the dashboard and stored in the DB."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting

DEFAULT_PERSONA = """You are {agent_name}, the AI phone assistant for BongoHive, Zambia's innovation hub in Lusaka, and its AI Lab.
You speak like a warm, competent, professional Zambian customer service representative: friendly, concise and clear.

How to speak:
- This is a phone call. Keep each reply short: one to three sentences, under 60 words, unless the caller asks for detail.
- Use plain spoken English. No lists, bullet points, markdown, emojis, URLs or abbreviations that are hard to say aloud. Say "K" amounts as "kwacha".
- Ask one question at a time. Use the caller's name if you know it.
- Do not repeat the greeting or introduce yourself again.

Accuracy rules (critical):
- Only state facts about BongoHive that appear in the KNOWLEDGE section. Never invent prices, dates, policies, phone numbers, names or commitments.
- If the KNOWLEDGE does not answer the question, say honestly that you don't have that information, and offer to take the caller's name and number so a staff member can call back, or to transfer them during office hours.
- You may make general conversation and explain general concepts, but never present guesses as BongoHive facts.
- Never promise anything on BongoHive's behalf (admission, funding, discounts, refunds).
- Do not say something is unavailable, not offered or free unless the KNOWLEDGE says so. If the KNOWLEDGE says a price is not published, say exactly that and offer to have the team share pricing; never state a number.

Capturing details:
- When a caller wants something followed up, politely collect their name, the best phone number, and a short description. Confirm the details back once.

Control tags (append at the very end of your reply, never explain them):
- [TRANSFER] when the caller asks for a human, is upset, or the issue needs staff and you have offered a transfer and they agreed.
- [END_CALL] when the conversation is clearly finished and you have said goodbye.
"""


class VoiceSettings(BaseModel):
    engine: Literal["kokoro", "piper"] = "kokoro"
    voice: str = "af_heart"
    speed: float = Field(1.0, ge=0.6, le=1.5)
    lang: str = "en-us"


class TurnTakingSettings(BaseModel):
    endpoint_silence_ms: int = Field(650, ge=200, le=3000)
    min_speech_ms: int = Field(180, ge=60, le=1000)
    vad_threshold: float = Field(0.5, ge=0.1, le=0.95)
    barge_in_enabled: bool = True
    barge_in_min_ms: int = Field(350, ge=100, le=2000)
    silence_reprompt_s: float = Field(9.0, ge=3, le=60)
    max_reprompts: int = Field(2, ge=0, le=5)
    max_call_minutes: int = Field(15, ge=1, le=120)
    max_utterance_s: float = Field(25.0, ge=5, le=60)


class OperatingHours(BaseModel):
    timezone: str = "Africa/Lusaka"
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon=0
    open: str = "08:00"
    close: str = "17:00"
    after_hours_message: str = (
        "Our office is currently closed, but I can still answer questions or take your details "
        "so the team can call you back on the next working day."
    )


class EscalationSettings(BaseModel):
    transfer_enabled: bool = True
    transfer_only_in_hours: bool = True
    keywords: list[str] = Field(default_factory=lambda: [
        "speak to a human", "speak to a person", "real person", "talk to someone",
        "human being", "speak to someone", "customer care", "manager", "operator",
    ])
    max_unanswered: int = Field(2, ge=1, le=10)
    transfer_message: str = "Of course. Please hold while I connect you to a member of the team."
    unavailable_message: str = (
        "I'm sorry, no one is available to take the call right now. I've logged your request "
        "and a team member will call you back."
    )


class RetentionSettings(BaseModel):
    transcripts_days: int = Field(90, ge=1, le=3650)
    recordings_days: int = Field(30, ge=1, le=3650)
    redact_contact_details_in_transcripts: bool = False


class AgentSettings(BaseModel):
    agent_name: str = "Buzz"
    persona: str = DEFAULT_PERSONA
    greeting_inbound: str = (
        "Hello, thank you for calling BongoHive. I'm {agent_name}, an AI assistant. How can I help you today?"
    )
    greeting_outbound: str = (
        "Hello, this is {agent_name}, an AI assistant calling on behalf of BongoHive in Lusaka. "
        "Is this a good time to talk?"
    )
    ai_disclosure_required: bool = True
    recording_enabled: bool = False
    recording_disclosure: str = "This call may be recorded for quality and training purposes."
    goodbye: str = "Thank you for calling BongoHive. Have a lovely day. Goodbye."
    reprompt: str = "Are you still there?"
    no_response_goodbye: str = "I haven't heard anything, so I'll end the call now. Please call us back anytime. Goodbye."
    fallback_error: str = (
        "I'm sorry, I'm having a technical problem right now. Let me take a note so a member "
        "of the team can call you back."
    )
    languages: list[str] = Field(default_factory=lambda: ["en"])
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    turn_taking: TurnTakingSettings = Field(default_factory=TurnTakingSettings)
    hours: OperatingHours = Field(default_factory=OperatingHours)
    escalation: EscalationSettings = Field(default_factory=EscalationSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    llm_temperature: float = Field(0.3, ge=0, le=1.5)
    llm_max_tokens: int = Field(160, ge=32, le=1024)
    knowledge_top_k: int = Field(4, ge=1, le=10)
    knowledge_min_score: float = Field(0.58, ge=0, le=1)
    history_turns: int = Field(10, ge=2, le=40)


_cache: AgentSettings | None = None


async def get_agent_settings(db: AsyncSession) -> AgentSettings:
    global _cache
    if _cache is not None:
        return _cache
    row = await db.get(Setting, "agent")
    _cache = AgentSettings.model_validate(row.value) if row else AgentSettings()
    return _cache


async def save_agent_settings(db: AsyncSession, settings: AgentSettings, by: str) -> AgentSettings:
    global _cache
    row = await db.get(Setting, "agent")
    if row is None:
        row = Setting(key="agent", value={})
        db.add(row)
    row.value = settings.model_dump()
    row.updated_by = by
    await db.commit()
    _cache = settings
    return settings


def clear_cache() -> None:
    global _cache
    _cache = None
