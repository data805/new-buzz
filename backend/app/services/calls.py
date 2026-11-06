"""Persistence helpers for calls, transcripts and opt-outs."""
from __future__ import annotations

import logging
import re

from sqlalchemy import select

from ..db import SessionLocal, utcnow
from ..models import Call, CallTurn, CampaignContact, Contact
from .events import bus
from .settings import get_agent_settings

log = logging.getLogger(__name__)


async def create_call(**fields) -> Call:
    async with SessionLocal() as db:
        call = Call(**fields)
        db.add(call)
        await db.commit()
        return call


async def update_call(call_id: str, **fields) -> None:
    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None:
            return
        for k, v in fields.items():
            setattr(call, k, v)
        await db.commit()


_PHONE_RE = re.compile(r"(\+?\d[\d\s-]{7,}\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


async def add_turn(call_id: str, role: str, text: str, interrupted: bool = False,
                   latency: dict | None = None, sources: list | None = None) -> None:
    async with SessionLocal() as db:
        settings = await get_agent_settings(db)
        if settings.retention.redact_contact_details_in_transcripts and role == "user":
            text = _EMAIL_RE.sub("[email]", _PHONE_RE.sub("[number]", text))
        db.add(CallTurn(call_id=call_id, role=role, text=text, interrupted=interrupted,
                        latency=latency or {}, sources=sources or []))
        await db.commit()


async def get_or_create_contact(phone: str, name: str = "") -> Contact | None:
    if not phone or not phone.startswith("+"):
        return None
    async with SessionLocal() as db:
        c = (await db.execute(select(Contact).where(Contact.phone == phone))).scalar_one_or_none()
        if c is None:
            c = Contact(phone=phone, name=name)
            db.add(c)
            await db.commit()
        return c


async def opt_out(phone: str, source: str, call_id: str | None = None) -> None:
    """Record a do-not-call request. Always honoured, regardless of workflows."""
    if not phone:
        return
    async with SessionLocal() as db:
        c = (await db.execute(select(Contact).where(Contact.phone == phone))).scalar_one_or_none()
        if c is None:
            c = Contact(phone=phone)
            db.add(c)
        c.consent_status = "revoked"
        c.opted_out_at = utcnow()
        c.consent_source = source[:255]
        await db.flush()
        rows = (await db.execute(select(CampaignContact).where(
            CampaignContact.contact_id == c.id, CampaignContact.status.in_(["pending", "no_answer", "busy", "failed", "voicemail"])
        ))).scalars()
        for cc in rows:
            cc.status = "opted_out"
            cc.next_attempt_at = None
        await db.commit()
    log.info("Contact opted out", extra={"ctx": {"phone_suffix": phone[-3:], "source": source}})
    await bus.publish("contact.opted_out", {"phone": phone, "source": source, "call_id": call_id})
