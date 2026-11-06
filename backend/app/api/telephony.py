"""Telephony endpoints: the Asterisk dialplan hook, number checks and the
machine-to-machine integration API (API-key authenticated, e.g. for n8n)."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import ApiKey, Call, User
from ..outbound.dialer import CallNotAllowed, place_call
from ..security import audit, rate_limit, require_api_key, require_role
from ..services.events import EVENT_TYPES, bus
from ..telephony.base import get_provider
from ..telephony.numbers import InvalidNumber, describe, mask, normalise
from .campaigns import ContactIn, contact_out, upsert_contact

router = APIRouter(tags=["telephony"])


@router.get("/api/telephony/asterisk/register", response_class=PlainTextResponse,
            dependencies=[Depends(rate_limit("asterisk", 600, 60))])
async def asterisk_register(token: str, direction: str = "inbound", caller: str = "", did: str = "", channel: str = "",
                            uuid: str = "", amd: str = ""):
    """Called by the dialplan via CURL(). Returns a UUID for AudioSocket, BUSY or HANGUP."""
    s = get_settings()
    expected = s.asterisk_callback_token.get_secret_value()
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "bad token")
    provider = get_provider()
    if provider.name != "asterisk":
        return "HANGUP"
    return await provider.register_call(direction, caller, did, channel, uuid, amd)  # type: ignore[attr-defined]


@router.get("/api/telephony/status")
async def telephony_status(_: User = Depends(require_role("viewer"))):
    s = get_settings()
    return {"provider": s.telephony_provider, "health": await get_provider().health(),
            "outbound_caller_id": s.outbound_caller_id, "trunk_endpoint": s.asterisk_trunk_endpoint,
            "dial_format": s.asterisk_dial_format, "human_transfer_target": bool(s.human_transfer_target),
            "allowed_country_codes": s.allowed_country_codes, "audiosocket_port": s.audiosocket_port}


@router.get("/api/telephony/check-number")
async def check_number(number: str = Query(..., max_length=40), _: User = Depends(require_role("viewer"))):
    try:
        e164 = normalise(number)
    except InvalidNumber as e:
        return {"valid": False, "error": str(e)}
    return {"e164": e164, **describe(e164)}


# ---------------------------------------------------------------- integration API (v1)
class TriggerCallIn(BaseModel):
    phone: str
    purpose: str = Field(min_length=3, max_length=2000)
    idempotency_key: str | None = Field(None, max_length=200)


@router.post("/api/v1/calls", tags=["integrations"])
async def v1_trigger_call(body: TriggerCallIn, request: Request, key: ApiKey = Depends(require_api_key("calls:create")),
                          db: AsyncSession = Depends(get_db)):
    """Request an outbound call to a consented contact. Idempotent when idempotency_key is given."""
    try:
        call = await place_call(body.phone, purpose=body.purpose, requested_by=f"api:{key.name}",
                                idempotency_key=f"api:{key.id}:{body.idempotency_key}" if body.idempotency_key else None)
    except CallNotAllowed as e:
        raise HTTPException(422, str(e)) from e
    await audit(db, "api.call_requested", mask(call.to_number), actor=f"api:{key.name}", request=request)
    await db.commit()
    return {"call_id": call.id, "status": call.status}


@router.get("/api/v1/calls/{call_id}", tags=["integrations"])
async def v1_get_call(call_id: str, _: ApiKey = Depends(require_api_key("calls:read")), db: AsyncSession = Depends(get_db)):
    c = await db.get(Call, call_id)
    if not c:
        raise HTTPException(404, "Call not found")
    return {"id": c.id, "direction": c.direction, "status": c.status, "outcome": c.outcome, "duration_s": c.duration_s,
            "summary": c.summary, "intent": c.intent, "action_items": c.action_items, "extracted": c.extracted}


class EventIn(BaseModel):
    type: str = "custom"
    data: dict = {}
    idempotency_key: str | None = Field(None, max_length=200)


@router.post("/api/v1/events", tags=["integrations"])
async def v1_event(body: EventIn, key: ApiKey = Depends(require_api_key("events:create"))):
    if body.type != "custom" and not body.type.startswith("custom."):
        raise HTTPException(400, "Only 'custom' events can be posted externally")
    payload = {**body.data, "source": f"api:{key.name}"}
    if body.idempotency_key:
        payload["idempotency_key"] = body.idempotency_key
    await bus.publish("custom", payload, wait=True)
    return {"accepted": True}


@router.post("/api/v1/contacts", tags=["integrations"])
async def v1_contact(body: ContactIn, request: Request, key: ApiKey = Depends(require_api_key("contacts:write")),
                     db: AsyncSession = Depends(get_db)):
    c = await upsert_contact(db, body, f"api:{key.name}")
    await audit(db, "api.contact_upserted", mask(c.phone), actor=f"api:{key.name}", details={"consent": c.consent_status}, request=request)
    await db.commit()
    return contact_out(c)
