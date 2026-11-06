"""After a call: summarise, store extraction, update campaign progress, fire events."""
from __future__ import annotations

import logging

from ..agent.summarizer import summarise_call
from ..db import SessionLocal
from ..models import Call
from .events import bus

log = logging.getLogger(__name__)


async def process_completed_call(call_id: str, transcript: list[dict], meta, outcome: str, escalated: bool) -> None:
    try:
        data = await summarise_call(transcript)
    except Exception:
        log.exception("Post-call summary failed", extra={"ctx": {"call_id": call_id}})
        data = {"summary": "", "action_items": []}
    phone = meta.from_number if meta.direction == "inbound" else meta.to_number
    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None:
            return
        call.summary = data.get("summary", "")
        call.intent = data.get("intent", "")
        call.sentiment = data.get("sentiment", "")
        call.resolved = data.get("resolved")
        call.action_items = data.get("action_items", [])
        call.extracted = data
        await db.commit()
    if meta.campaign_id:
        from ..outbound.dialer import record_campaign_outcome

        await record_campaign_outcome(call_id, outcome)
    payload = {
        "call_id": call_id, "direction": meta.direction, "channel": meta.channel, "phone": phone,
        "outcome": outcome, "escalated": escalated, "campaign_id": meta.campaign_id, "is_test": meta.is_test,
        "extracted": data,
    }
    await bus.publish("call.completed", payload)
