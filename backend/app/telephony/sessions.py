"""Glue: build CallMeta for a call row and run a CallSession on a transport."""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Call, Campaign, Contact
from ..services.settings import get_agent_settings
from ..voice.pipeline import CallMeta, CallSession, Transport

log = logging.getLogger(__name__)


async def meta_for_outbound(call_id: str, channel: str) -> CallMeta:
    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None:
            raise LookupError(call_id)
        contact = await db.get(Contact, call.contact_id) if call.contact_id else None
        camp = await db.get(Campaign, call.campaign_id) if call.campaign_id else None
        return CallMeta(
            direction="outbound", channel=channel, from_number=call.from_number, to_number=call.to_number,
            contact_id=call.contact_id, contact_name=contact.name if contact else "",
            campaign_id=call.campaign_id, campaign_name=camp.name if camp else "",
            campaign_script=camp.script if camp else (call.extracted or {}).get("purpose", ""),
            opening_line=camp.opening_line if camp else "",
            voicemail_action=camp.voicemail_action if camp else "hangup",
            voicemail_message=camp.voicemail_message if camp else "",
            call_id=call_id, provider_call_id=call.provider_call_id,
        )


async def meta_for_inbound(channel: str, from_number: str, to_number: str, provider_call_id: str | None = None,
                           is_test: bool = False) -> CallMeta:
    name, contact_id = "", None
    if from_number.startswith("+"):
        async with SessionLocal() as db:
            c = (await db.execute(select(Contact).where(Contact.phone == from_number))).scalar_one_or_none()
            if c:
                name, contact_id = c.name, c.id
    return CallMeta(direction="inbound", channel=channel, from_number=from_number, to_number=to_number,
                    contact_id=contact_id, contact_name=name, provider_call_id=provider_call_id, is_test=is_test)


async def run_session(transport: Transport, meta: CallMeta) -> CallSession:
    async with SessionLocal() as db:
        settings = await get_agent_settings(db)
    session = CallSession(transport, meta, settings)
    transport.session = session  # type: ignore[attr-defined]
    await session.run()
    return session
