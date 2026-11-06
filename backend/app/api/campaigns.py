from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import Campaign, CampaignContact, Contact, User
from ..outbound.dialer import CallNotAllowed, in_window, place_call
from ..security import audit, require_role
from ..services.calls import opt_out
from ..services.maintenance import erase_number
from ..telephony.numbers import InvalidNumber, describe, mask, normalise

router = APIRouter(prefix="/api", tags=["outbound"])
viewer, operator, manager, admin = (require_role(r) for r in ("viewer", "operator", "manager", "admin"))


def contact_out(c: Contact) -> dict:
    return {"id": c.id, "phone": c.phone, "name": c.name, "email": c.email, "organisation": c.organisation,
            "consent_status": c.consent_status, "consent_source": c.consent_source,
            "consent_at": c.consent_at.isoformat() if c.consent_at else None,
            "opted_out_at": c.opted_out_at.isoformat() if c.opted_out_at else None, "tags": c.tags, "notes": c.notes,
            "operator": describe(c.phone).get("operator", ""), "created_at": c.created_at.isoformat()}


# ------------------------------------------------------------------ contacts
@router.get("/contacts")
async def list_contacts(q: str | None = None, consent: str | None = None, limit: int = 500,
                        _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    stmt = select(Contact).order_by(Contact.created_at.desc()).limit(min(limit, 5000))
    if q:
        stmt = stmt.where(Contact.phone.like(f"%{q}%") | Contact.name.like(f"%{q}%") | Contact.organisation.like(f"%{q}%"))
    if consent:
        stmt = stmt.where(Contact.consent_status == consent)
    return [contact_out(c) for c in (await db.execute(stmt)).scalars()]


class ContactIn(BaseModel):
    phone: str
    name: str = ""
    email: str = ""
    organisation: str = ""
    consent_status: str = "unknown"
    consent_source: str = ""
    tags: list[str] = []
    notes: str = ""


async def upsert_contact(db: AsyncSession, body: ContactIn, by: str) -> Contact:
    try:
        phone = normalise(body.phone)
    except InvalidNumber as e:
        raise HTTPException(400, str(e)) from e
    if body.consent_status not in ("granted", "unknown", "revoked"):
        raise HTTPException(400, "consent_status must be granted, unknown or revoked")
    if body.consent_status == "granted" and not body.consent_source.strip():
        raise HTTPException(400, "consent_source is required when consent is granted (how/when consent was given)")
    c = (await db.execute(select(Contact).where(Contact.phone == phone))).scalar_one_or_none()
    if c is None:
        c = Contact(phone=phone)
        db.add(c)
    if c.consent_status == "revoked" and body.consent_status == "granted":
        raise HTTPException(409, f"{mask(phone)} has opted out; consent cannot be re-granted by import")
    for f in ("name", "email", "organisation", "notes"):
        if getattr(body, f):
            setattr(c, f, getattr(body, f)[:255] if f != "notes" else getattr(body, f))
    c.tags = body.tags or c.tags
    if body.consent_status != c.consent_status:
        c.consent_status = body.consent_status
        c.consent_source = f"{body.consent_source} (recorded by {by})"[:255]
        c.consent_at = utcnow()
        if body.consent_status == "revoked":
            c.opted_out_at = utcnow()
    return c


@router.post("/contacts", status_code=201)
async def create_contact(body: ContactIn, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    c = await upsert_contact(db, body, user.email)
    await audit(db, "contact.upserted", mask(c.phone), user=user, details={"consent": c.consent_status}, request=request)
    await db.commit()
    return contact_out(c)


@router.post("/contacts/import")
async def import_contacts(request: Request, file: UploadFile = File(...), consent_attestation: str = Form(""),
                          campaign_id: str = Form(""), user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    """CSV columns: phone (required), name, email, organisation, consent (yes/no), consent_source.
    Rows marked consent=yes require `consent_attestation` describing how consent was obtained."""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "phone" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(400, "CSV must have a 'phone' column")
    camp = await db.get(Campaign, campaign_id) if campaign_id else None
    if campaign_id and not camp:
        raise HTTPException(404, "Campaign not found")
    if camp and camp.status not in ("draft", "pending_approval"):
        raise HTTPException(400, "Contacts can only be added to draft campaigns")
    imported, errors, added = 0, [], 0
    for i, row in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        consent = row.get("consent", "").lower() in ("yes", "y", "true", "1", "granted")
        if consent and not consent_attestation.strip():
            raise HTTPException(400, "Rows with consent=yes need a consent attestation (how consent was obtained)")
        try:
            c = await upsert_contact(db, ContactIn(
                phone=row.get("phone", ""), name=row.get("name", ""), email=row.get("email", ""),
                organisation=row.get("organisation", ""), consent_status="granted" if consent else "unknown",
                consent_source=row.get("consent_source") or consent_attestation), user.email)
            await db.flush()
            imported += 1
            if camp:
                exists = await db.scalar(select(func.count()).select_from(CampaignContact).where(
                    CampaignContact.campaign_id == camp.id, CampaignContact.contact_id == c.id))
                if not exists:
                    db.add(CampaignContact(campaign_id=camp.id, contact_id=c.id))
                    added += 1
        except HTTPException as e:
            errors.append({"row": i, "error": e.detail})
        if len(errors) > 200:
            break
    await audit(db, "contacts.imported", file.filename or "", user=user,
                details={"imported": imported, "errors": len(errors), "campaign": campaign_id, "attestation": consent_attestation[:300]},
                request=request)
    await db.commit()
    return {"imported": imported, "added_to_campaign": added, "errors": errors}


@router.post("/contacts/{contact_id}/opt-out")
async def contact_opt_out(contact_id: str, request: Request, user: User = Depends(operator), db: AsyncSession = Depends(get_db)):
    c = await db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "Contact not found")
    await opt_out(c.phone, source=f"dashboard:{user.email}")
    await audit(db, "contact.opted_out", mask(c.phone), user=user, request=request)
    await db.commit()
    return {"ok": True}


class EraseIn(BaseModel):
    phone: str


@router.post("/privacy/erase")
async def erase(body: EraseIn, request: Request, user: User = Depends(admin), db: AsyncSession = Depends(get_db)):
    try:
        phone = normalise(body.phone)
    except InvalidNumber as e:
        raise HTTPException(400, str(e)) from e
    result = await erase_number(phone)
    await audit(db, "privacy.erased", mask(phone), user=user, details=result, request=request)
    await db.commit()
    return result


# ------------------------------------------------------------------ campaigns
class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    purpose: str = Field("follow_up", max_length=50)
    script: str = Field("", max_length=10000)
    opening_line: str = Field("", max_length=1000)
    max_concurrent: int = Field(1, ge=1, le=20)
    calls_per_hour: int = Field(10, ge=1, le=500)
    max_attempts: int = Field(3, ge=1, le=5)
    retry_delay_minutes: int = Field(60, ge=5, le=10080)
    window_start: str = Field("09:00", pattern=r"^\d\d:\d\d$")
    window_end: str = Field("17:00", pattern=r"^\d\d:\d\d$")
    days: list[int] = [0, 1, 2, 3, 4]
    voicemail_action: str = Field("hangup", pattern="^(hangup|leave_message)$")
    voicemail_message: str = Field("", max_length=1000)


def campaign_out(c: Campaign, counts: dict | None = None) -> dict:
    d = {k: getattr(c, k) for k in ("id", "name", "purpose", "script", "opening_line", "status", "created_by", "approved_by",
                                     "max_concurrent", "calls_per_hour", "max_attempts", "retry_delay_minutes",
                                     "window_start", "window_end", "days", "voicemail_action", "voicemail_message", "stats")}
    d["approved_at"] = c.approved_at.isoformat() if c.approved_at else None
    d["created_at"] = c.created_at.isoformat()
    d["in_window_now"] = in_window(c)
    d["counts"] = counts or {}
    return d


async def _counts(db: AsyncSession, cid: str) -> dict:
    rows = (await db.execute(select(CampaignContact.status, func.count()).where(CampaignContact.campaign_id == cid)
                             .group_by(CampaignContact.status))).all()
    d = {s: n for s, n in rows}
    d["total"] = sum(d.values())
    return d


@router.get("/campaigns")
async def list_campaigns(_: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    camps = (await db.execute(select(Campaign).order_by(Campaign.created_at.desc()))).scalars().all()
    s = get_settings()
    return {"outbound_enabled": s.outbound_enabled, "allow_bulk_outbound": s.allow_bulk_outbound,
            "bulk_threshold": s.bulk_threshold, "items": [campaign_out(c, await _counts(db, c.id)) for c in camps]}


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignIn, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    c = Campaign(**body.model_dump(), created_by=user.email)
    db.add(c)
    await audit(db, "campaign.created", c.name, user=user, request=request)
    await db.commit()
    return campaign_out(c)


@router.get("/campaigns/{cid}")
async def get_campaign(cid: str, _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    c = await db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    contacts = (await db.execute(select(CampaignContact).where(CampaignContact.campaign_id == cid)
                                 .order_by(CampaignContact.id).limit(2000))).scalars().all()
    out = campaign_out(c, await _counts(db, cid))
    out["contacts"] = [{"id": cc.id, "contact_id": cc.contact_id, "phone": cc.contact.phone, "name": cc.contact.name,
                        "consent_status": cc.contact.consent_status, "status": cc.status, "attempts": cc.attempts,
                        "last_outcome": cc.last_outcome, "last_call_id": cc.last_call_id,
                        "next_attempt_at": cc.next_attempt_at.isoformat() if cc.next_attempt_at else None} for cc in contacts]
    return out


@router.put("/campaigns/{cid}")
async def update_campaign(cid: str, body: CampaignIn, request: Request, user: User = Depends(manager),
                          db: AsyncSession = Depends(get_db)):
    c = await db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if c.status not in ("draft", "pending_approval", "paused"):
        raise HTTPException(400, "Only draft or paused campaigns can be edited")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    if c.status != "draft":  # any edit after approval requires re-approval
        c.status, c.approved_by, c.approved_at = "draft", None, None
    await audit(db, "campaign.updated", c.name, user=user, request=request)
    await db.commit()
    return campaign_out(c)


class AddContactsIn(BaseModel):
    contact_ids: list[str]


@router.post("/campaigns/{cid}/contacts")
async def add_contacts(cid: str, body: AddContactsIn, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    c = await db.get(Campaign, cid)
    if not c or c.status not in ("draft", "pending_approval"):
        raise HTTPException(400, "Campaign not found or not in draft")
    added = 0
    for contact_id in body.contact_ids:
        if not await db.get(Contact, contact_id):
            continue
        exists = await db.scalar(select(func.count()).select_from(CampaignContact).where(
            CampaignContact.campaign_id == cid, CampaignContact.contact_id == contact_id))
        if not exists:
            db.add(CampaignContact(campaign_id=cid, contact_id=contact_id))
            added += 1
    await db.commit()
    return {"added": added}


TRANSITIONS = {
    "submit": ({"draft"}, "pending_approval", "manager"),
    "approve": ({"pending_approval"}, "approved", "admin"),
    "reject": ({"pending_approval"}, "draft", "admin"),
    "start": ({"approved", "paused"}, "running", "manager"),
    "pause": ({"running"}, "paused", "operator"),
    "cancel": ({"draft", "pending_approval", "approved", "running", "paused"}, "cancelled", "manager"),
}


@router.post("/campaigns/{cid}/{action}")
async def campaign_action(cid: str, action: str, request: Request, user: User = Depends(operator),
                          db: AsyncSession = Depends(get_db)):
    from ..security import has_role

    if action not in TRANSITIONS:
        raise HTTPException(404, "Unknown action")
    allowed_from, to, role = TRANSITIONS[action]
    if not has_role(user, role):
        raise HTTPException(403, f"'{action}' requires role '{role}'")
    c = await db.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if c.status not in allowed_from:
        raise HTTPException(400, f"Cannot {action} a campaign in status '{c.status}'")
    s = get_settings()
    counts = await _counts(db, cid)
    if action in ("submit", "approve"):
        if counts.get("total", 0) == 0:
            raise HTTPException(400, "Campaign has no contacts")
        no_consent = await db.scalar(select(func.count()).select_from(CampaignContact).join(Contact).where(
            CampaignContact.campaign_id == cid, Contact.consent_status != "granted"))
        if no_consent:
            raise HTTPException(400, f"{no_consent} contact(s) have no recorded consent; remove them or record consent first")
        if not c.script.strip():
            raise HTTPException(400, "Campaign needs a script / call purpose for the agent")
    if action == "approve":
        if counts["total"] > s.bulk_threshold and not s.allow_bulk_outbound:
            raise HTTPException(400, f"Campaign has {counts['total']} contacts (> BULK_THRESHOLD={s.bulk_threshold}). "
                                     "Bulk outbound calling is disabled (ALLOW_BULK_OUTBOUND=false).")
        if c.created_by == user.email and user.role != "admin":
            raise HTTPException(403, "A campaign must be approved by an admin")
        c.approved_by, c.approved_at = user.email, utcnow()
    if action == "start" and not s.outbound_enabled:
        raise HTTPException(400, "Outbound calling is disabled on this server (OUTBOUND_ENABLED=false)")
    c.status = to
    await audit(db, f"campaign.{action}", c.name, user=user, request=request)
    await db.commit()
    return campaign_out(c, counts)


class SingleCallIn(BaseModel):
    phone: str
    purpose: str = Field(min_length=3, max_length=2000)


@router.post("/calls/outbound")
async def single_call(body: SingleCallIn, request: Request, user: User = Depends(manager), db: AsyncSession = Depends(get_db)):
    """Place one authorised call to a consented contact (e.g. appointment confirmation)."""
    try:
        call = await place_call(body.phone, purpose=body.purpose, requested_by=user.email)
    except CallNotAllowed as e:
        raise HTTPException(400, str(e)) from e
    await audit(db, "call.outbound_requested", mask(call.to_number), user=user, details={"purpose": body.purpose[:200]}, request=request)
    await db.commit()
    return {"call_id": call.id, "status": call.status}
