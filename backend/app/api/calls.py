from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db, utcnow
from ..models import Call, Callback, Enquiry, Feedback, Ticket, User
from ..security import audit, has_role, require_role
from ..services.events import live

router = APIRouter(prefix="/api", tags=["calls"])
viewer = require_role("viewer")
operator = require_role("operator")


def call_out(c: Call, full: bool = False) -> dict:
    d = {
        "id": c.id, "direction": c.direction, "channel": c.channel, "from_number": c.from_number, "to_number": c.to_number,
        "status": c.status, "outcome": c.outcome, "hangup_cause": c.hangup_cause, "campaign_id": c.campaign_id,
        "contact_id": c.contact_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "duration_s": c.duration_s, "turn_count": c.turn_count, "summary": c.summary, "intent": c.intent,
        "sentiment": c.sentiment, "resolved": c.resolved, "has_recording": bool(c.recording_path),
        "metrics": c.metrics,
    }
    if full:
        d.update(action_items=c.action_items, extracted=c.extracted, error=c.error, turns=[
            {"id": t.id, "ts": t.ts.isoformat(), "role": t.role, "text": t.text, "interrupted": t.interrupted,
             "latency": t.latency, "sources": t.sources} for t in c.turns])
    return d


@router.get("/calls")
async def list_calls(
    q: str | None = None, direction: str | None = None, outcome: str | None = None, channel: str | None = None,
    date_from: str | None = None, date_to: str | None = None, campaign_id: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    _: User = Depends(viewer), db: AsyncSession = Depends(get_db),
):
    stmt = select(Call)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Call.from_number.like(like), Call.to_number.like(like), Call.summary.like(like),
                              Call.intent.like(like), Call.id == q))
    if direction:
        stmt = stmt.where(Call.direction == direction)
    if outcome:
        stmt = stmt.where(Call.outcome == outcome)
    if channel:
        stmt = stmt.where(Call.channel == channel)
    if campaign_id:
        stmt = stmt.where(Call.campaign_id == campaign_id)
    if date_from:
        stmt = stmt.where(Call.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Call.created_at < datetime.fromisoformat(date_to) + timedelta(days=1))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.execute(stmt.order_by(Call.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars()
    return {"total": total, "page": page, "page_size": page_size, "items": [call_out(c) for c in rows]}


@router.get("/calls/{call_id}")
async def get_call(call_id: str, _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    c = (await db.execute(select(Call).where(Call.id == call_id).options(selectinload(Call.turns)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Call not found")
    return call_out(c, full=True)


@router.get("/calls/{call_id}/recording")
async def get_recording(call_id: str, request: Request, user: User = Depends(require_role("manager")),
                        db: AsyncSession = Depends(get_db)):
    c = await db.get(Call, call_id)
    if not c or not c.recording_path or not Path(c.recording_path).exists():
        raise HTTPException(404, "No recording for this call")
    await audit(db, "recording.accessed", call_id, user=user, request=request)
    await db.commit()
    return FileResponse(c.recording_path, media_type="audio/wav", filename=f"call-{call_id}.wav")


@router.delete("/calls/{call_id}")
async def delete_call(call_id: str, request: Request, user: User = Depends(require_role("admin")),
                      db: AsyncSession = Depends(get_db)):
    c = await db.get(Call, call_id)
    if not c:
        raise HTTPException(404, "Call not found")
    if c.recording_path:
        Path(c.recording_path).unlink(missing_ok=True)
    await db.delete(c)
    await audit(db, "call.deleted", call_id, user=user, request=request)
    await db.commit()
    return {"ok": True}


@router.get("/live")
async def live_calls(_: User = Depends(operator)):
    return live.snapshot()


@router.get("/stats/overview")
async def overview(days: int = Query(14, ge=1, le=365), _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    base = select(Call).where(Call.created_at >= since)
    calls = (await db.execute(base)).scalars().all()
    answered = [c for c in calls if c.answered_at]
    missed = [c for c in calls if c.outcome in ("no_answer", "busy", "failed", "rejected")]
    success = [c for c in calls if c.outcome in ("completed", "transferred") and c.resolved is not False]
    tz = ZoneInfo("Africa/Lusaka")
    by_day: dict[str, dict] = {}
    for i in range(days):
        d = (utcnow() - timedelta(days=days - 1 - i)).astimezone(tz).strftime("%Y-%m-%d")
        by_day[d] = {"date": d, "inbound": 0, "outbound": 0, "missed": 0}
    for c in calls:
        d = c.created_at.astimezone(tz).strftime("%Y-%m-%d") if c.created_at.tzinfo else c.created_at.strftime("%Y-%m-%d")
        if d in by_day:
            by_day[d][c.direction] += 1
            if c in missed:
                by_day[d]["missed"] += 1
    outcomes: dict[str, int] = {}
    intents: dict[str, int] = {}
    for c in calls:
        outcomes[c.outcome or c.status] = outcomes.get(c.outcome or c.status, 0) + 1
        if c.intent:
            intents[c.intent] = intents.get(c.intent, 0) + 1
    lat = [c.metrics.get("first_audio_ms", {}).get("avg") for c in answered if c.metrics and c.metrics.get("first_audio_ms")]
    durations = [c.duration_s for c in answered if c.duration_s]
    open_counts = {
        "enquiries": await db.scalar(select(func.count()).select_from(Enquiry).where(Enquiry.status == "new")),
        "tickets": await db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status != "resolved")),
        "callbacks": await db.scalar(select(func.count()).select_from(Callback).where(Callback.status == "pending")),
    }
    return {
        "days": days, "total": len(calls), "answered": len(answered), "missed": len(missed), "successful": len(success),
        "inbound": sum(1 for c in calls if c.direction == "inbound"), "outbound": sum(1 for c in calls if c.direction == "outbound"),
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else 0,
        "avg_first_audio_ms": round(sum(lat) / len(lat)) if lat else None,
        "escalated": sum(1 for c in calls if c.outcome in ("escalated", "transferred")),
        "by_day": list(by_day.values()), "outcomes": outcomes, "intents": intents, "open": open_counts,
        "live": len(live.snapshot()),
    }


# ------------------------------------------------------------------ business records
RECORDS = {"enquiries": Enquiry, "tickets": Ticket, "callbacks": Callback, "feedback": Feedback}
STATUSES = {"enquiries": ["new", "contacted", "closed"], "tickets": ["open", "in_progress", "resolved"],
            "callbacks": ["pending", "done", "cancelled"]}


def record_out(r) -> dict:
    d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


@router.get("/records/{kind}")
async def list_records(kind: str, status: str | None = None, limit: int = Query(200, le=1000),
                       _: User = Depends(viewer), db: AsyncSession = Depends(get_db)):
    model = RECORDS.get(kind)
    if not model:
        raise HTTPException(404, "Unknown record type")
    q = select(model).order_by(model.created_at.desc()).limit(limit)
    if status and hasattr(model, "status"):
        q = q.where(model.status == status)
    return [record_out(r) for r in (await db.execute(q)).scalars()]


class RecordUpdate(BaseModel):
    status: str = Field(max_length=20)


@router.patch("/records/{kind}/{record_id}")
async def update_record(kind: str, record_id: str, body: RecordUpdate, request: Request,
                        user: User = Depends(operator), db: AsyncSession = Depends(get_db)):
    model = RECORDS.get(kind)
    if not model or kind not in STATUSES:
        raise HTTPException(404, "Unknown record type")
    if body.status not in STATUSES[kind]:
        raise HTTPException(400, f"status must be one of {STATUSES[kind]}")
    r = await db.get(model, record_id)
    if not r:
        raise HTTPException(404, "Not found")
    r.status = body.status
    await audit(db, f"{kind}.status", record_id, user=user, details={"status": body.status}, request=request)
    await db.commit()
    return record_out(r)
