"""Outbound calling: compliance gates, campaign scheduler, retries and outcomes.

Every outbound call - from a campaign, a workflow or the API - goes through
`check_can_call`, which enforces: global kill switch (OUTBOUND_ENABLED), allowed
destination countries, recorded consent, opt-out list, minimum spacing between
calls to the same number, hourly volume cap and concurrent-call capacity."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import Call, Campaign, CampaignContact, Contact
from ..services.events import bus
from ..telephony.base import OriginateRequest, capacity, get_provider
from ..telephony.numbers import InvalidNumber, is_allowed_destination, normalise

log = logging.getLogger(__name__)
TZ = ZoneInfo("Africa/Lusaka")
FINAL_STATUSES = {"completed", "opted_out", "skipped", "exhausted"}
RETRYABLE = {"no_answer", "busy", "failed", "voicemail"}


class CallNotAllowed(Exception):
    pass


async def check_can_call(db, phone: str, require_consent: bool = True, ignore_spacing: bool = False) -> Contact:
    s = get_settings()
    if not s.outbound_enabled:
        raise CallNotAllowed("Outbound calling is disabled (set OUTBOUND_ENABLED=true after compliance review)")
    try:
        phone = normalise(phone)
    except InvalidNumber as e:
        raise CallNotAllowed(str(e)) from e
    if not is_allowed_destination(phone):
        raise CallNotAllowed(f"Destination country not allowed (ALLOWED_COUNTRY_CODES={s.allowed_country_codes})")
    contact = (await db.execute(select(Contact).where(Contact.phone == phone))).scalar_one_or_none()
    if contact is None:
        raise CallNotAllowed("Number is not a known contact; import it with recorded consent first")
    if contact.consent_status == "revoked" or contact.opted_out_at:
        raise CallNotAllowed("Contact has opted out of calls")
    if require_consent and contact.consent_status != "granted":
        raise CallNotAllowed("No recorded consent for this contact")
    since = utcnow() - timedelta(hours=s.outbound_min_hours_between_calls)
    if not ignore_spacing:
        recent = await db.scalar(select(func.count()).select_from(Call).where(
            Call.to_number == phone, Call.direction == "outbound", Call.created_at >= since,
            Call.outcome.in_(["completed", "transferred", "escalated", "opted_out", ""]),
            Call.status != "failed",
        ))
        if recent:
            raise CallNotAllowed(f"Number was already called in the last {s.outbound_min_hours_between_calls}h")
    hour_count = await db.scalar(select(func.count()).select_from(Call).where(
        Call.direction == "outbound", Call.created_at >= utcnow() - timedelta(hours=1)))
    if hour_count >= s.outbound_max_calls_per_hour:
        raise CallNotAllowed("Hourly outbound call limit reached")
    if capacity().available <= 0:
        raise CallNotAllowed("All call channels are busy")
    return contact


async def place_call(phone: str, *, campaign_id: str | None = None, purpose: str = "", requested_by: str = "system",
                     idempotency_key: str | None = None, require_consent: bool = True, ignore_spacing: bool = False,
                     campaign_contact_id: int | None = None) -> Call:
    """Validate and start an outbound call. Raises CallNotAllowed."""
    s = get_settings()
    async with SessionLocal() as db:
        if idempotency_key:
            existing = (await db.execute(select(Call).where(Call.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing:
                return existing
        contact = await check_can_call(db, phone, require_consent=require_consent, ignore_spacing=ignore_spacing)
        call = Call(direction="outbound", channel=get_provider().name, from_number=s.outbound_caller_id,
                    to_number=contact.phone, contact_id=contact.id, campaign_id=campaign_id, status="queued",
                    idempotency_key=idempotency_key, extracted={"purpose": purpose, "requested_by": requested_by})
        db.add(call)
        try:
            await db.flush()
            if campaign_contact_id is not None:
                cc = await db.get(CampaignContact, campaign_contact_id)
                cc.last_call_id = call.id
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = (await db.execute(select(Call).where(Call.idempotency_key == idempotency_key))).scalar_one()
            return existing
    log.info("Placing outbound call", extra={"ctx": {"call_id": call.id, "campaign": campaign_id, "by": requested_by}})
    await get_provider().originate(OriginateRequest(call.id, call.to_number, s.outbound_caller_id))
    return call


async def on_dial_outcome(call_id: str, outcome: str, cause: str) -> None:
    """Provider callback for attempts that never connected to the agent."""
    async with SessionLocal() as db:
        call = await db.get(Call, call_id)
        if call is None:
            return
        call.status = "failed" if outcome == "failed" else "completed"
        call.outcome = outcome
        call.hangup_cause = cause[:100]
        call.ended_at = utcnow()
        await db.commit()
        campaign_id, phone = call.campaign_id, call.to_number
    log.info("Outbound attempt not connected", extra={"ctx": {"call_id": call_id, "outcome": outcome, "cause": cause}})
    if campaign_id:
        await record_campaign_outcome(call_id, outcome)
    await bus.publish("call.failed", {"call_id": call_id, "outcome": outcome, "cause": cause, "phone": phone,
                                      "campaign_id": campaign_id})


async def record_campaign_outcome(call_id: str, outcome: str) -> None:
    async with SessionLocal() as db:
        cc = (await db.execute(select(CampaignContact).where(CampaignContact.last_call_id == call_id))).scalar_one_or_none()
        if cc is None:
            return
        camp = await db.get(Campaign, cc.campaign_id)
        cc.last_outcome = outcome
        if outcome in RETRYABLE and not (outcome == "voicemail" and camp.voicemail_action == "leave_message"):
            if cc.attempts >= camp.max_attempts:
                cc.status = "exhausted"
                cc.next_attempt_at = None
            else:
                cc.status = outcome
                cc.next_attempt_at = utcnow() + timedelta(minutes=camp.retry_delay_minutes * cc.attempts)
        elif outcome == "opted_out":
            cc.status = "opted_out"
        else:
            cc.status = "completed"
            cc.next_attempt_at = None
        stats = dict(camp.stats or {})
        stats[outcome] = stats.get(outcome, 0) + 1
        camp.stats = stats
        await db.commit()


def in_window(camp: Campaign, now: datetime | None = None) -> bool:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return now.weekday() in (camp.days or []) and camp.window_start <= now.strftime("%H:%M") < camp.window_end


class Dialer:
    """Background scheduler for running campaigns (single instance)."""

    def __init__(self, interval_s: float = 5.0) -> None:
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None
        self.last_tick: datetime | None = None
        self.last_error = ""

    def start(self) -> None:
        get_provider().on_dial_outcome = on_dial_outcome
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)
                log.exception("Dialer tick failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime | None = None) -> int:
        """One scheduling pass. Returns number of calls placed."""
        self.last_tick = utcnow()
        if not get_settings().outbound_enabled:
            return 0
        placed = 0
        async with SessionLocal() as db:
            camps = (await db.execute(select(Campaign).where(Campaign.status == "running"))).scalars().all()
        for camp in camps:
            placed += await self._tick_campaign(camp, now)
        return placed

    async def _tick_campaign(self, camp: Campaign, now: datetime | None) -> int:
        placed = 0
        async with SessionLocal() as db:
            remaining = await db.scalar(select(func.count()).select_from(CampaignContact).where(
                CampaignContact.campaign_id == camp.id, CampaignContact.status.notin_(FINAL_STATUSES)))
            if remaining == 0:
                c = await db.get(Campaign, camp.id)
                c.status = "completed"
                await db.commit()
                await bus.publish("campaign.completed", {"campaign_id": camp.id, "name": camp.name, "stats": c.stats})
                return 0
            if not in_window(camp, now):
                return 0
            active = await db.scalar(select(func.count()).select_from(CampaignContact).where(
                CampaignContact.campaign_id == camp.id, CampaignContact.status == "in_progress"))
            last_hour = await db.scalar(select(func.count()).select_from(Call).where(
                Call.campaign_id == camp.id, Call.created_at >= utcnow() - timedelta(hours=1)))
            slots = min(camp.max_concurrent - active, camp.calls_per_hour - last_hour, capacity().available)
            if slots <= 0:
                return 0
            due = (await db.execute(select(CampaignContact).where(
                CampaignContact.campaign_id == camp.id,
                CampaignContact.status.in_(["pending", *RETRYABLE]),
                CampaignContact.attempts < camp.max_attempts,
                or_(CampaignContact.next_attempt_at.is_(None), CampaignContact.next_attempt_at <= utcnow()),
            ).order_by(CampaignContact.attempts, CampaignContact.id).limit(slots))).scalars().all()
            for cc in due:
                phone = cc.contact.phone
                try:
                    await check_can_call(db, phone, ignore_spacing=cc.attempts > 0)
                except CallNotAllowed as e:
                    reason = str(e)
                    if "opted out" in reason or "consent" in reason or "not allowed" in reason:
                        cc.status = "opted_out" if "opted out" in reason else "skipped"
                        cc.last_outcome = reason[:30]
                        await db.commit()
                    else:
                        cc.next_attempt_at = utcnow() + timedelta(minutes=15)
                        await db.commit()
                    continue
                cc.status = "in_progress"
                cc.attempts += 1
                await db.commit()
                try:
                    call = await place_call(phone, campaign_id=camp.id, purpose=camp.purpose,
                                            requested_by=f"campaign:{camp.id}",
                                            idempotency_key=f"campaign:{camp.id}:{cc.id}:{cc.attempts}",
                                            ignore_spacing=cc.attempts > 1, campaign_contact_id=cc.id)
                    await db.refresh(cc)
                    placed += 1
                except CallNotAllowed as e:
                    cc.status = "pending"
                    cc.attempts -= 1
                    cc.next_attempt_at = utcnow() + timedelta(minutes=15)
                    cc.last_outcome = str(e)[:30]
                    await db.commit()
        return placed


dialer = Dialer()
